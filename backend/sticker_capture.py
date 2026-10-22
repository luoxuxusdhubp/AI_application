"""
从微信聊天窗口截取表情包

免费版 wxauto4 的表情消息没有 download()，微信本地的表情缓存又是加密的，
所以这里直接对聊天窗口里“完整可见”的表情消息截屏，再自动裁剪：
- 去掉头像所在的一侧
- 按聊天背景色裁出表情本体
- 把背景抠成透明，得到可以直接发送的 PNG

只能得到静态图（动图只取当前帧），且只能取到聊天里出现过的表情。
截屏需要微信窗口在最前、不被遮挡；调用期间用户不能操作微信。
"""

import ctypes
import hashlib
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image, ImageChops, ImageDraw, ImageGrab

from . import wx_history

logger = logging.getLogger(__name__)

AVATAR_WIDTH = 85        # 消息项里头像（含边距）占的宽度，像素
BG_TOLERANCE = 14        # 与背景色相差不超过这个值的像素算背景
EDGE_LEFT = 6            # 消息区左边缘的细边线
EDGE_RIGHT = 18          # 消息区右边缘的滚动条
EDGE_VERTICAL = 2
MIN_SIDE = 24            # 裁出来小于这个尺寸的不要（多半是误检）
THUMB_SIZE = 32
SIMILAR_THRESHOLD = 14   # 缩略图平均灰度差小于这个值，视为同一个表情的不同帧
MARGIN = 4


def _set_dpi_aware() -> None:
    """让截屏坐标与 UIAutomation 返回的控件坐标使用同一套（物理）像素。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _background_color(img: Image.Image) -> tuple:
    colors = img.convert("RGB").getcolors(maxcolors=img.width * img.height)
    return max(colors, key=lambda item: item[0])[1]


def crop_sticker(item_img: Image.Image, is_self: bool) -> Optional[Image.Image]:
    """从一条消息的截图里裁出表情本体；找不到时返回 None。"""
    img = item_img.convert("RGB")
    # 自己发的头像在右边，对方的在左边。左边缘有细边线、右边缘有滚动条，
    # 上下留一点边，都排除在检测范围之外，避免被当成“表情”。
    left = EDGE_LEFT if is_self else AVATAR_WIDTH
    right = img.width - (AVATAR_WIDTH if is_self else EDGE_RIGHT)
    box = (left, EDGE_VERTICAL, max(left + 1, right), max(EDGE_VERTICAL + 1, img.height - EDGE_VERTICAL))
    region = img.crop(box)
    bg = _background_color(region)
    diff = ImageChops.difference(region, Image.new("RGB", region.size, bg)).convert("L")
    mask = diff.point(lambda v: 255 if v > BG_TOLERANCE else 0)
    bbox = mask.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        return None
    left, top = max(0, left - MARGIN), max(0, top - MARGIN)
    right, bottom = min(region.width, right + MARGIN), min(region.height, bottom + MARGIN)
    return region.crop((left, top, right, bottom))


def make_transparent(img: Image.Image) -> Image.Image:
    """把与四角相连的背景色抠成透明（表情内部同色的区域不受影响）。"""
    rgba = img.convert("RGBA")
    for corner in ((0, 0), (rgba.width - 1, 0), (0, rgba.height - 1), (rgba.width - 1, rgba.height - 1)):
        ImageDraw.floodfill(rgba, corner, (0, 0, 0, 0), thresh=BG_TOLERANCE)
    return rgba


def _thumb(rgba: Image.Image) -> List[int]:
    """合成到白底后缩成小灰度图，用来判断两张图是不是同一个表情。"""
    canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    canvas.alpha_composite(rgba.convert("RGBA"))
    return list(canvas.convert("L").resize((THUMB_SIZE, THUMB_SIZE)).getdata())


def is_similar(a: List[int], b: List[int]) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < SIMILAR_THRESHOLD


def capture_stickers(
    wx,
    out_dir: os.PathLike,
    max_scrolls: int = 12,
    only_self: Optional[bool] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict:
    """从当前聊天窗口底部向上，截取表情消息并保存为 PNG。

    wx: 已经 ChatWith 到目标聊天、并滚到底部的 wxauto4.WeChat。
    only_self: True 只取自己发的，False 只取对方发的，None 都取。
    返回 {"saved": [路径...], "skipped_duplicate": n, "seen": n}。
    """
    _set_dpi_aware()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    msgbox = wx_history.get_msgbox(wx)
    if msgbox is None:
        raise RuntimeError("找不到微信消息列表控件，请确认已打开聊天窗口")

    saved: List[str] = []
    # 动图的每一帧像素都不一样，哈希去不掉；再按缩略图相似度去重，也和已有文件比对
    known = []
    for existing in sorted(out.glob("*.png"))[:500]:
        try:
            known.append(_thumb(Image.open(existing)))
        except Exception:
            continue
    seen_hashes = set()
    duplicates = 0
    seen = 0
    for window in wx_history.iter_history_windows(wx, max_scrolls=max_scrolls, wheel_times=2, should_stop=should_stop):
        view = msgbox.BoundingRectangle
        for msg in window:
            if str(getattr(msg, "type", "")) != "emotion":
                continue
            is_self = str(getattr(msg, "attr", "")).casefold() == "self"
            if only_self is not None and is_self != only_self:
                continue
            try:
                rect = msg.control.BoundingRectangle
            except Exception:
                continue
            # 只截完整落在可视区里的，被裁掉一半的等滚动后再截
            if rect.top < view.top or rect.bottom > view.bottom or rect.width() <= 0 or rect.height() <= 0:
                continue
            seen += 1
            shot = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
            sticker = crop_sticker(shot, is_self)
            if sticker is None:
                logger.warning("表情截图里没有找到表情本体，已跳过")
                continue
            digest = hashlib.md5(sticker.tobytes()).hexdigest()[:12]
            target = out / f"{digest}.png"
            if digest in seen_hashes or target.exists():
                duplicates += 1
                continue
            seen_hashes.add(digest)
            transparent = make_transparent(sticker)
            thumb = _thumb(transparent)
            if any(is_similar(thumb, other) for other in known):
                duplicates += 1
                continue
            known.append(thumb)
            transparent.save(target)
            saved.append(str(target))
    logger.info("表情截取完成: 新增 %s 张，重复 %s 张", len(saved), duplicates)
    return {"saved": saved, "skipped_duplicate": duplicates, "seen": seen}
