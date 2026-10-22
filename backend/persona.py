"""
聊天记录风格分析模块

思路：
- 把某个聊天对象的历史消息按“对方说 → 我怎么回”切成问答对
- 统计我自己的说话习惯（句长、语气词、表情、常用开头）生成风格画像
- 回复时检索与当前消息最相似的历史问答对，作为少样本示例交给大模型

设计目标：纯统计、纯 CPU、无额外依赖；语料不足时优雅降级为不注入。
"""

import json
import logging
import re
import sqlite3
from collections import Counter
from contextlib import closing
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

from .db import DB_PATH

logger = logging.getLogger(__name__)

# 画像至少需要这么多条“我发的消息”才可信
MIN_SELF_MESSAGES = 30
MAX_CORPUS_ROWS = 5000
SHORT_MESSAGE_LEN = 8

_WX_EMOJI_RE = re.compile(r"\[[一-鿿A-Za-z0-9]{1,6}\]")
_UNICODE_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_ENDING_PARTICLES = "啊呀啦嘛哈呢吧哦噢诶欸嘿嗯哇"
_SUFFIX_RE = re.compile(r"[（(][^（）()]*[）)]$")


def chat_key(chat_name: str) -> str:
    """聊天对象归一化：忽略大小写和备注名末尾的括号补充信息。"""
    text = (chat_name or "").strip().casefold()
    return _SUFFIX_RE.sub("", text).strip() or text


# ---------------------------------------------------------------- 语料存取

def extract_rows(messages: Iterable) -> List[Tuple[int, str, str]]:
    """把 wxauto4 消息对象转为 (is_self, content, time) 行，只保留文本消息。"""
    rows: List[Tuple[int, str, str]] = []
    for msg in messages or []:
        content = str(getattr(msg, "content", "") or "").strip()
        msg_type = str(getattr(msg, "type", "") or "").casefold()
        sender = str(getattr(msg, "sender", "") or "").strip().casefold()
        attr = str(getattr(msg, "attr", "") or "").strip().casefold()
        if not content or (msg_type and msg_type != "text"):
            continue
        if attr in {"system", "time", "tickle", "系统"} or sender in {"system", "系统", "微信"}:
            continue
        is_self = sender == "self" or attr == "self" or bool(getattr(msg, "is_self", False))
        if not is_self and not sender:
            continue
        rows.append((1 if is_self else 0, content[:500], str(getattr(msg, "time", "") or "")))
    return rows


def save_corpus(chat_name: str, rows: List[Tuple[int, str, str]]) -> Dict:
    """保存语料。新读取的记录比库里少时保留旧数据，避免用更短的窗口覆盖。"""
    key = chat_key(chat_name)
    rows = rows[-MAX_CORPUS_ROWS:]
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
        old = conn.execute(
            "SELECT COUNT(*) FROM chat_corpus WHERE chat_name=?", (key,)
        ).fetchone()[0]
        if len(rows) < old:
            return {"saved": False, "total": old, "reason": "新读取的消息少于已保存的语料，已保留原数据"}
        conn.execute("DELETE FROM chat_corpus WHERE chat_name=?", (key,))
        conn.executemany(
            "INSERT INTO chat_corpus(chat_name, is_self, content, msg_time, seq) VALUES (?,?,?,?,?)",
            [(key, is_self, content, msg_time, i) for i, (is_self, content, msg_time) in enumerate(rows)],
        )
        conn.commit()
    return {"saved": True, "total": len(rows), "reason": ""}


def load_corpus(chat_name: str) -> List[Tuple[int, str]]:
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
        rows = conn.execute(
            "SELECT is_self, content FROM chat_corpus WHERE chat_name=? ORDER BY seq",
            (chat_key(chat_name),),
        ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


# ---------------------------------------------------------------- 问答对

def build_pairs(rows: List[Tuple[int, str]]) -> List[Tuple[str, str]]:
    """连续同一方的消息合并成一轮；对方一轮后紧跟我的一轮即为一个问答对。"""
    turns: List[Tuple[int, List[str]]] = []
    for is_self, content in rows:
        if turns and turns[-1][0] == is_self:
            turns[-1][1].append(content)
        else:
            turns.append((is_self, [content]))
    pairs: List[Tuple[str, str]] = []
    for prev, cur in zip(turns, turns[1:]):
        if prev[0] == 0 and cur[0] == 1:
            pairs.append(("\n".join(prev[1]), "\n".join(cur[1])))
    return pairs


# ---------------------------------------------------------------- 风格画像

def analyze(rows: List[Tuple[int, str]]) -> Dict:
    mine = [c for is_self, c in rows if is_self]
    others = [c for is_self, c in rows if not is_self]
    profile: Dict = {"self_count": len(mine), "other_count": len(others), "reliable": len(mine) >= MIN_SELF_MESSAGES}
    if not mine:
        return profile

    lengths = [len(c) for c in mine]
    profile["avg_len"] = round(sum(lengths) / len(lengths), 1)
    profile["median_len"] = median(lengths)
    profile["short_ratio"] = round(sum(1 for n in lengths if n <= SHORT_MESSAGE_LEN) / len(lengths), 2)

    ending = Counter(c.rstrip("~～!！.。…")[-1:] for c in mine if c.rstrip("~～!！.。…"))
    profile["top_endings"] = [
        [ch, round(n / len(mine), 2)] for ch, n in ending.most_common(8) if ch in _ENDING_PARTICLES
    ][:4]

    emoji = Counter()
    with_emoji = 0
    for c in mine:
        found = _WX_EMOJI_RE.findall(c) + _UNICODE_EMOJI_RE.findall(c)
        if found:
            with_emoji += 1
            emoji.update(found)
    profile["emoji_ratio"] = round(with_emoji / len(mine), 2)
    profile["top_emoji"] = [e for e, _ in emoji.most_common(5)]

    profile["laugh_ratio"] = round(sum(1 for c in mine if "哈哈" in c) / len(mine), 2)

    openers = Counter(c[:2] for c in mine if len(c) >= 2)
    profile["top_openers"] = [o for o, n in openers.most_common(4) if n >= 3]
    return profile


def render_profile(chat_name: str, profile: Dict) -> str:
    """把画像转成可直接放进系统提示词的中文描述。"""
    if not profile.get("reliable"):
        return ""
    lines = [f"【你与「{chat_name}」的聊天风格画像（基于 {profile['self_count']} 条你发过的消息统计）】"]
    lines.append(
        f"- 单条消息平均 {profile['avg_len']} 字，{int(profile['short_ratio'] * 100)}% 不超过 "
        f"{SHORT_MESSAGE_LEN} 字，保持这种长度，不要写成长段落。"
    )
    if profile.get("top_endings"):
        text = "、".join(f"“{ch}”" for ch, _ in profile["top_endings"])
        lines.append(f"- 常以 {text} 这类语气词结尾。")
    if profile.get("laugh_ratio", 0) >= 0.1:
        lines.append(f"- 约 {int(profile['laugh_ratio'] * 100)}% 的消息带“哈哈”，气氛轻松时可以用。")
    if profile.get("emoji_ratio", 0) >= 0.05 and profile.get("top_emoji"):
        lines.append(
            f"- 约 {int(profile['emoji_ratio'] * 100)}% 的消息带表情，常用：{' '.join(profile['top_emoji'])}。"
        )
    elif profile.get("emoji_ratio", 0) < 0.05:
        lines.append("- 很少使用表情符号，不要滥用。")
    if profile.get("top_openers"):
        lines.append(f"- 常见开头：{'、'.join(profile['top_openers'])}。")
    return "\n".join(lines)


def refresh_profile(chat_name: str) -> Dict:
    profile = analyze(load_corpus(chat_name))
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
        conn.execute(
            "INSERT INTO persona_profiles(chat_name, profile_json, updated_at) "
            "VALUES (?, ?, DATETIME('now','localtime')) "
            "ON CONFLICT(chat_name) DO UPDATE SET profile_json=excluded.profile_json, "
            "updated_at=excluded.updated_at",
            (chat_key(chat_name), json.dumps(profile, ensure_ascii=False)),
        )
        conn.commit()
    return profile


def load_profile(chat_name: str) -> Optional[Dict]:
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
        row = conn.execute(
            "SELECT profile_json, updated_at FROM persona_profiles WHERE chat_name=?",
            (chat_key(chat_name),),
        ).fetchone()
    if not row:
        return None
    try:
        profile = json.loads(row[0])
    except Exception:
        return None
    profile["updated_at"] = row[1]
    return profile


# ---------------------------------------------------------------- 少样本示例

def retrieve_examples(chat_name: str, incoming: str, k: int = 3, min_score: float = 0.15) -> List[Tuple[str, str]]:
    """找出历史上与当前消息最相似的几个“对方说 → 我回”问答对。"""
    from .ai_qa import _char_bigrams, _cosine  # 延迟导入，避免与 ai_qa 循环依赖

    pairs = build_pairs(load_corpus(chat_name))
    if not pairs:
        return []
    q_vec = Counter(_char_bigrams(incoming))
    scored: List[Tuple[float, Tuple[str, str]]] = []
    seen = set()
    for other, mine in pairs:
        if len(mine) > 80 or len(other) > 120 or (other, mine) in seen:
            continue
        seen.add((other, mine))
        score = _cosine(q_vec, Counter(_char_bigrams(other)))
        if score >= min_score:
            scored.append((score, (other, mine)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [pair for _, pair in scored[:k]]


def build_persona_prompt(chat_name: str, incoming: str, with_examples: bool = True) -> str:
    """返回追加到系统提示词的风格说明；没有语料时返回空串。"""
    try:
        profile = load_profile(chat_name)
        parts: List[str] = []
        if profile:
            text = render_profile(chat_name, profile)
            if text:
                parts.append(text)
        if with_examples:
            examples = retrieve_examples(chat_name, incoming)
            if examples:
                shots = "\n".join(
                    f"对方：{other.replace(chr(10), ' / ')}\n你：{mine.replace(chr(10), ' / ')}"
                    for other, mine in examples
                )
                parts.append(f"【你过去在相似情境下的真实回复，学习语气，不要照抄】\n{shots}")
        return "\n".join(parts)
    except Exception:
        logger.exception("构建风格提示失败，已跳过")
        return ""
