"""
本地知识库语义问答模块

功能：
- 从 SQLite 的 qa_kb 表加载问答数据
- 使用字符双字组余弦相似度进行语义检索
- 可选：通过大模型优化回答
- 返回最匹配的答案和元数据

设计目标：
- 轻量级，最小依赖
- 纯 CPU 运行
- 优雅降级
"""

import logging
import os
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import re
import sqlite3
from collections import Counter
from contextlib import closing
from math import sqrt
from typing import Dict, List, Optional, Tuple

from .db import DB_PATH

logger = logging.getLogger(__name__)


def _normalize_text(s: str) -> str:
    """文本标准化：保留中文、字母、数字，规范化空格"""
    try:
        s = s.strip()
        # 移除标点符号，保留中文、字母、数字和空格
        s = re.sub(r"[^\w\u4e00-\u9fff\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s
    except Exception:
        return s or ""


def _char_bigrams(s: str) -> List[str]:
    """生成字符双字组：用于中文和混合文本的分词近似"""
    s = _normalize_text(s)
    # 移除空格，专注于连续字符
    s = s.replace(" ", "")
    if len(s) <= 1:
        return [s] if s else []
    return [s[i : i + 2] for i in range(len(s) - 1)]


def _cosine(c1: Counter, c2: Counter) -> float:
    """计算两个词频向量的余弦相似度"""
    if not c1 or not c2:
        return 0.0
    # 点积
    dot = 0.0
    for k, v in c1.items():
        if k in c2:
            dot += v * c2[k]
    # 向量模
    mag1 = sqrt(sum(v * v for v in c1.values()))
    mag2 = sqrt(sum(v * v for v in c2.values()))
    if mag1 == 0.0 or mag2 == 0.0:
        return 0.0
    return dot / (mag1 * mag2)


def _load_kb(conn: sqlite3.Connection) -> List[Dict]:
    """从数据库加载所有知识库条目"""
    conn.row_factory = sqlite3.Row
    rows = conn.cursor().execute(
        "SELECT id, question, answer, created_at, updated_at FROM qa_kb ORDER BY id DESC"
    ).fetchall()
    items: List[Dict] = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "question": r["question"] or "",
                "answer": r["answer"] or "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return items


def retrieve_best(question: str) -> Tuple[Optional[Dict], float]:
    """检索最匹配的知识库条目，返回条目和相似度得分"""
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            kb = _load_kb(conn)
        if not kb:
            return None, 0.0

        q_vec = Counter(_char_bigrams(question))
        best: Optional[Dict] = None
        best_score = 0.0
        for item in kb:
            c_vec = Counter(_char_bigrams(item["question"]))
            score = _cosine(q_vec, c_vec)
            # 精确匹配加分
            if item["question"].strip() == question.strip():
                score += 0.2
            elif question.strip() in item["question"].strip():
                score += 0.1
            if score > best_score:
                best_score = score
                best = item
        return best, best_score
    except Exception as e:
        logger.exception("知识库检索错误: %s", e)
        return None, 0.0


def _read_env_local_values() -> Dict[str, str]:
    """从 .env.local 文件读取模型配置。

    当前默认使用智谱 GLM，同时兼容旧版 DeepSeek 配置名。
    """
    values: Dict[str, str] = {}
    try:
        # 向上查找 .env.local 文件
        candidates = [
            Path(__file__).resolve().parent.parent / ".env.local",
            Path(__file__).resolve().parent.parent.parent / ".env.local",
        ]
        for p in candidates:
            if p.exists():
                txt = p.read_text(encoding="utf-8", errors="ignore")
                for line in txt.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, value = line.split("=", 1)
                    name = name.strip()
                    if name in {
                        "ZHIPU_API_KEY",
                        "ZHIPU_API_BASE",
                        "ZHIPU_MODEL",
                        "GLM_API_KEY",
                        "GLM_API_BASE",
                        "GLM_MODEL",
                        "DEEPSEEK_API_KEY",
                        "DEEPSEEK_API_BASE",
                        "DEEPSEEK_MODEL",
                    }:
                        values[name] = value.strip().strip('"').strip("'")
                break
    except Exception:
        pass
    return values


def _read_env_local_key() -> Optional[str]:
    """兼容旧调用：从 .env.local 读取模型 API 密钥。"""
    values = _read_env_local_values()
    return (
        values.get("ZHIPU_API_KEY")
        or values.get("GLM_API_KEY")
        or values.get("DEEPSEEK_API_KEY")
    )


def _generate_with_llm(
    question: str,
    kb_answer: str,
    system: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
    """调用智谱 GLM（兼容旧版 DeepSeek 配置）生成优化回复。"""
    local_values = _read_env_local_values()
    zhipu_key = os.getenv("ZHIPU_API_KEY") or local_values.get("ZHIPU_API_KEY")
    glm_key = os.getenv("GLM_API_KEY") or local_values.get("GLM_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY") or local_values.get("DEEPSEEK_API_KEY")
    key = (
        zhipu_key
        or glm_key
        or deepseek_key
        or _read_env_local_key()
    )
    if not key:
        return None
    configured_base = (
        os.getenv("ZHIPU_API_BASE")
        or os.getenv("GLM_API_BASE")
        or os.getenv("DEEPSEEK_API_BASE")
        or local_values.get("ZHIPU_API_BASE")
        or local_values.get("GLM_API_BASE")
        or local_values.get("DEEPSEEK_API_BASE")
    )
    if configured_base:
        base = configured_base
    elif zhipu_key or glm_key:
        base = "https://open.bigmodel.cn/api/paas/v4"
    elif deepseek_key:
        base = "https://api.deepseek.com/v1"
    else:
        base = "https://open.bigmodel.cn/api/paas/v4"
    base = base.rstrip("/")
    configured_model = (
        os.getenv("ZHIPU_MODEL")
        or os.getenv("GLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or local_values.get("ZHIPU_MODEL")
        or local_values.get("GLM_MODEL")
        or local_values.get("DEEPSEEK_MODEL")
    )
    if configured_model:
        model = configured_model
    elif zhipu_key or glm_key:
        model = "glm-4-flash"
    else:
        model = "deepseek-chat"
    sys_prompt = (system or _get_default_system_prompt()).strip()
    messages = [{"role": "system", "content": sys_prompt}]
    # 只传递最近的有效对话，避免上下文无限增长；当前问题单独放在最后。
    for item in (history or [])[-12:]:
        role = item.get("role") if isinstance(item, dict) else None
        content = item.get("content") if isinstance(item, dict) else None
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()[:1000]})
    messages.append({
        "role": "user",
        "content": f"问题：{question}\n知识库参考答案：{kb_answer}\n请用中文回答，并尽量保持简洁与可执行。",
    })
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1000,
    }
    url = f"{base}/chat/completions"
    req = Request(url, data=json.dumps(payload).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            # 智谱 GLM 兼容 OpenAI 格式: choices[0].message.content
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content")
            )
            if isinstance(content, str) and content.strip():
                return content.strip()
    except (HTTPError, URLError, TimeoutError) as e:
        logger.warning("GLM API 调用失败: %s", e)
    except Exception as e:
        logger.exception("GLM API 异常: %s", e)
    return None


def answer_question(
    question: str,
    system: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict:
    """核心接口：根据问题返回答案和元数据
    
    返回字典包含: answer, matched_id, matched_question, score
    """
    q = (question or "").strip()
    if not q:
        return {
            "answer": "请输入问题",
            "matched_id": None,
            "matched_question": None,
            "score": 0.0,
        }

    item, score = retrieve_best(q)
    if item and score >= 0.05:
        kb_answer = item["answer"]
        # 尝试用大模型优化答案
        llm_ans = _generate_with_llm(q, kb_answer, system or _get_default_system_prompt(), history)
        final_answer = llm_ans if (llm_ans and len(llm_ans) >= 5) else kb_answer
        return {
            "answer": final_answer,
            "matched_id": item["id"],
            "matched_question": item["question"],
            "score": score,
        }
    # 未命中知识库：让大模型直接回答（仍受系统提示词约束）
    llm_ans = _generate_with_llm(q, "（未命中知识库）", system or _get_default_system_prompt(), history)
    return {
        "answer": llm_ans or "暂未命中知识库，请补充条目或调整问题",
        "matched_id": None,
        "matched_question": None,
        "score": score,
    }


def _parse_reply_options(raw: str, count: int = 3) -> List[str]:
    """把模型输出整理成候选回复，兼容编号列表和逐行输出。"""
    text = (raw or "").strip()
    if not text:
        return []
    numbered = re.findall(
        r"(?:^|\n)\s*(?:\d+[.)]|[-*\u2022])\s*(.+?)(?=(?:\n\s*(?:\d+[.)]|[-*\u2022])\s*|\Z))",
        text,
        flags=re.S,
    )
    candidates = numbered or [line.strip() for line in text.splitlines() if line.strip()]
    result: List[str] = []
    for item in candidates:
        cleaned = re.sub(r"^\s*(?:\d+[\.\)、)]|[-•])\s*", "", item).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned and cleaned not in result:
            result.append(cleaned[:300])
        if len(result) >= count:
            break
    return result


def generate_reply_candidates(
    question: str,
    system: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    count: int = 3,
    chat_name: str = "",
    use_persona: bool = False,
    use_stickers: bool = False,
) -> List[Dict[str, Optional[str]]]:
    """生成供人工选择的多条候选回复，不执行发送。

    返回 [{"text": 回复文本, "emotion": 表情包情绪标识或 None}, ...]。
    use_persona: 注入该聊天对象的历史风格画像与相似情境示例。
    use_stickers: 让模型在合适的候选后标注表情包情绪。
    """
    from . import persona, stickers  # 延迟导入，避免循环依赖

    q = (question or "").strip()
    if not q:
        return []
    item, score = retrieve_best(q)
    kb_answer = item["answer"] if item and score >= 0.05 else "（未命中知识库）"
    option_system = (system or _get_default_system_prompt()).strip()
    if use_persona and chat_name:
        persona_prompt = persona.build_persona_prompt(chat_name, q)
        if persona_prompt:
            option_system += "\n\n" + persona_prompt
    option_system += (
        "\n现在请为当前微信消息生成恰好3条不同的候选回复。"
        "\n每条回复单独一行，只输出回复文本，不要编号、解释或前缀。"
        "\n保持自然、符合人设，并避免替用户做金钱、隐私、承诺等重要决定。"
    )
    if use_stickers:
        option_system += stickers.prompt_hint()
    raw = _generate_with_llm(q, kb_answer, option_system, history)

    candidates: List[Dict[str, Optional[str]]] = []
    for line in _parse_reply_options(raw or "", count + 2):
        text, emotion = stickers.split_sticker_tag(line)
        if not text:
            # 标记被模型单独放在一行时，归到上一条候选
            if emotion and candidates and not candidates[-1]["emotion"]:
                candidates[-1]["emotion"] = emotion if use_stickers else None
            continue
        candidates.append({"text": text, "emotion": emotion if use_stickers else None})
    candidates = candidates[:count]
    if len(candidates) >= count:
        return candidates

    # 模型返回格式不规范时，仍保留可审核的候选，避免监听进程直接发送。
    fallback = stickers.split_sticker_tag((raw or kb_answer or "我先想一下，晚点回复你。").strip())[0]
    if fallback and all(c["text"] != fallback for c in candidates):
        candidates.append({"text": fallback[:300], "emotion": None})
    defaults = [
        "收到啦，我先想一下，晚点和你说。",
        "好呀，我看到消息了，等我认真回复你。",
        "嗯嗯，我在呢，我们一会儿继续聊。",
    ]
    for default in defaults:
        if len(candidates) >= count:
            break
        if all(c["text"] != default for c in candidates):
            candidates.append({"text": default, "emotion": None})
    return candidates[:count]


# 可选的 DSPy 封装（用于未来的 LLM 编排）
try:
    import dspy  # type: ignore

    class KBQASignature(dspy.Signature):
        """知识库问答的 DSPy 签名"""
        question = dspy.InputField()
        answer = dspy.OutputField()

    class KBQAModule(dspy.Module):
        def __init__(self):
            super().__init__()

        def forward(self, question: str):
            res = answer_question(question)
            return dspy.Prediction(answer=res["answer"])  # type: ignore

except Exception:
    # DSPy 未安装，跳过 LLM 编排功能
    KBQASignature = None  # type: ignore
    KBQAModule = None  # type: ignore
def _get_default_system_prompt() -> str:
    """从数据库获取默认系统提示词，失败返回默认值"""
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.cursor().execute(
                "SELECT system_prompt FROM ai_settings ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["system_prompt"]:
                return str(row["system_prompt"]).strip()
    except Exception:
        pass
    return (
        "你是知识库问答助手，请基于给定的知识库答案，提供准确、礼貌、简洁的中文回复；"
        "如答案不足以覆盖问题，请说明需要更多信息或建议补充知识库。"
    )
