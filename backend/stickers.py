"""
本地表情包库

目录结构（把自己的表情图放进对应情绪文件夹，文件夹名可用英文标识或中文名）：

    backend/stickers/
        happy/ 或 开心/    *.gif *.png *.jpg
        sad/   或 难过/
        ...

回复时由大模型在候选末尾标注 [[sticker:情绪]]，缺失时用关键词兜底；
只有素材库里真的有对应图片才会附带表情包。
"""

import os
import random
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

STICKER_DIR = Path(os.getenv("STICKER_DIR") or (Path(__file__).resolve().parent / "stickers"))
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
# 截取下来、还没归类的表情包放这里；不参与发送，归类到情绪文件夹后才会被使用
INBOX_DIR = "inbox"

# 情绪标识 -> (中文名, 兜底关键词)
EMOTIONS: Dict[str, Tuple[str, List[str]]] = {
    "happy": ("开心", ["哈哈", "开心", "太好了", "嘿嘿", "好耶", "太棒"]),
    "love": ("爱你", ["爱你", "想你", "喜欢你", "么么", "亲亲", "宝贝"]),
    "cute": ("撒娇", ["嘛~", "好不好嘛", "人家", "求求"]),
    "comfort": ("抱抱", ["抱抱", "摸摸", "别难过", "心疼", "乖"]),
    "sad": ("难过", ["呜呜", "难过", "委屈", "哭了"]),
    "angry": ("生气", ["生气", "哼", "气死", "不理你"]),
    "tired": ("累了", ["好累", "困了", "累死", "想睡"]),
    "surprised": ("惊讶", ["天哪", "真的吗", "不会吧", "居然", "啊？"]),
    "thanks": ("谢谢", ["谢谢", "感谢", "辛苦了", "麻烦你"]),
    "sorry": ("抱歉", ["对不起", "抱歉", "我错了", "原谅我"]),
    "greeting": ("问候", ["早安", "晚安", "早上好", "晚上好", "在吗"]),
}
_LABEL_TO_KEY = {label: key for key, (label, _) in EMOTIONS.items()}

_TAG_RE = re.compile(r"[\[［]{1,2}\s*sticker\s*[:：]\s*([A-Za-z一-鿿_]+)\s*[\]］]{1,2}", re.I)


def _dir_emotion(dirname: str) -> Optional[str]:
    name = dirname.strip()
    if name.lower() in EMOTIONS:
        return name.lower()
    return _LABEL_TO_KEY.get(name)


def _files(emotion_dir: Path) -> List[Path]:
    try:
        return sorted(
            p for p in emotion_dir.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTS
        )
    except OSError:
        return []


def _scan() -> Dict[str, List[str]]:
    """扫描素材库，返回 情绪标识 -> 相对路径列表（相对 STICKER_DIR，使用 / 分隔）。"""
    library: Dict[str, List[str]] = {}
    if not STICKER_DIR.is_dir():
        return library
    for sub in sorted(STICKER_DIR.iterdir()):
        if not sub.is_dir():
            continue
        emotion = _dir_emotion(sub.name)
        if not emotion:
            continue
        for f in _files(sub):
            library.setdefault(emotion, []).append(f"{sub.name}/{f.name}")
    return library


def list_library() -> List[Dict]:
    library = _scan()
    return [
        {"emotion": key, "label": EMOTIONS[key][0], "count": len(files)}
        for key, files in library.items()
    ]


def available_emotions() -> List[str]:
    return list(_scan().keys())


def pick_sticker(emotion: Optional[str]) -> Optional[str]:
    """按情绪随机选一张，返回相对路径；素材库没有对应图片时返回 None。"""
    if not emotion:
        return None
    key = emotion.strip().lower()
    key = key if key in EMOTIONS else _LABEL_TO_KEY.get(emotion.strip())
    files = _scan().get(key or "")
    return random.choice(files) if files else None


def resolve_sticker(rel_path: str) -> Optional[Path]:
    """把相对路径解析为素材库内的真实文件；越界或不存在时返回 None。"""
    if not rel_path:
        return None
    try:
        root = STICKER_DIR.resolve()
        target = (root / rel_path).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    if target.is_file() and target.suffix.lower() in ALLOWED_EXTS:
        return target
    return None


def split_sticker_tag(text: str) -> Tuple[str, Optional[str]]:
    """去掉文本里的 [[sticker:xxx]] 标记，返回 (干净文本, 情绪标识)。"""
    emotion: Optional[str] = None
    match = _TAG_RE.search(text or "")
    if match:
        raw = match.group(1)
        key = raw.lower() if raw.lower() in EMOTIONS else _LABEL_TO_KEY.get(raw)
        emotion = key
    clean = _TAG_RE.sub("", text or "").strip()
    return clean, emotion


def detect_emotion(text: str) -> Optional[str]:
    """关键词兜底：模型没有标注时，根据回复内容推断情绪。命中关键词最多者胜出。"""
    best, best_hits = None, 0
    for key, (_, words) in EMOTIONS.items():
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best, best_hits = key, hits
    return best


def prompt_hint() -> str:
    """告诉模型可以用哪些情绪标记；素材库为空时返回空串。"""
    emotions = available_emotions()
    if not emotions:
        return ""
    names = "、".join(f"{k}({EMOTIONS[k][0]})" for k in emotions)
    return (
        f"\n如果某条候选适合配一个表情包，在该条末尾加上 [[sticker:情绪]]，情绪只能从这些里选：{names}。"
        "\n不是每条都需要表情包，没有明显情绪时不要加；标记不要出现在正文中间。"
    )
