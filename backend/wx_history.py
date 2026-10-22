"""
微信聊天历史翻页读取

免费版 wxauto4 没有“加载更多历史”接口，而且消息列表是虚拟化的：
GetAllMessage() 只返回当前窗口里可见的那几条。所以只能自己对消息列表
滚动鼠标滚轮，滚一步读一批，再把相邻的批次按重叠部分拼起来。

注意：滚动会移动鼠标并占用微信窗口，调用期间用户不能操作微信；
调用方还要先停掉自动回复监听进程，避免两个 UIAutomation 同时操作。
"""

import logging
import time
from types import SimpleNamespace
from typing import Callable, Iterator, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_FIELDS = ("type", "sender", "attr", "content", "time")


def get_msgbox(wx):
    """取出微信聊天窗口里的消息列表控件，取不到返回 None。"""
    api = getattr(wx, "_api", None)
    chat_api = getattr(api, "_chat_api", None)
    return getattr(chat_api, "msgbox", None)


def snapshot(msg) -> SimpleNamespace:
    """把消息对象的纯属性拷出来；控件滚出屏幕后会失效，不能一直持有。"""
    data = {name: str(getattr(msg, name, "") or "") for name in SNAPSHOT_FIELDS}
    data["is_self"] = bool(getattr(msg, "is_self", False))
    return SimpleNamespace(**data)


def _signature(msg) -> tuple:
    return (str(getattr(msg, "type", "")), str(getattr(msg, "sender", "")), str(getattr(msg, "content", ""))[:80])


def _layout(msgbox) -> tuple:
    """消息列表里各子控件的位置。滚动后位置没变，就说明已经到顶/到底了。

    不能用“读到的消息内容”判断：遇到一条几千像素高的超长消息时，
    可视区里一条消息也解析不出来，内容一直是空的，但列表其实还在滚动。
    """
    items = []
    try:
        for child in msgbox.GetChildren():
            rect = child.BoundingRectangle
            items.append((rect.top, rect.height()))
    except Exception:
        pass
    return tuple(items)


def scroll_to_bottom(wx, max_rounds: int = 15) -> None:
    """滚到聊天最底部（最新消息）。"""
    msgbox = get_msgbox(wx)
    if msgbox is None:
        return
    previous = None
    for _ in range(max_rounds):
        msgbox.WheelDown(wheelTimes=30, waitTime=0.2)
        time.sleep(0.5)
        current = _layout(msgbox)
        if current == previous:
            return
        previous = current


def iter_history_windows(
    wx,
    max_scrolls: int = 40,
    wheel_times: int = 3,
    settle: float = 0.6,
    stop_after_same: int = 2,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Iterator[list]:
    """先产出当前可见的消息，然后每向上滚一步产出一批。

    产出的是 wxauto4 的真实消息对象，仅在本次迭代内有效（可用来截图、读属性）。
    可视区里解析不出消息（例如被超长消息占满）时，自动加大滚动步长。
    """
    msgbox = get_msgbox(wx)
    if msgbox is None:
        raise RuntimeError("找不到微信消息列表控件，请确认已打开聊天窗口且微信版本兼容")

    same = 0
    window = wx.GetAllMessage() or []
    yield window
    for _ in range(max(0, max_scrolls)):
        if should_stop and should_stop():
            return
        before = _layout(msgbox)
        msgbox.WheelUp(wheelTimes=wheel_times if window else wheel_times * 4, waitTime=0.2)
        time.sleep(settle)
        window = wx.GetAllMessage() or []
        yield window
        same = same + 1 if _layout(msgbox) == before else 0
        if same >= stop_after_same:
            return


def merge_windows(older_first: List[list], newer: list) -> list:
    """把新读到的（更靠上的）一批消息拼到已有列表前面，去掉重叠部分。

    older_first: 已拼好的列表（从上到下）；newer: 新读到的一批（从上到下）。
    """
    if not older_first:
        return list(newer)
    max_k = min(len(older_first), len(newer))
    for k in range(max_k, 0, -1):
        if [_signature(m) for m in older_first[:k]] == [_signature(m) for m in newer[-k:]]:
            return list(newer[: len(newer) - k]) + older_first
    # 找不到重叠：滚得太快留下了空档，也照样接上，只会少几条而不会重复
    return list(newer) + older_first


def collect_history(wx, max_scrolls: int = 40, **kwargs) -> list:
    """向上翻历史并拼成按时间排序的消息快照列表（从旧到新）。"""
    merged: list = []
    for window in iter_history_windows(wx, max_scrolls=max_scrolls, **kwargs):
        merged = merge_windows(merged, [snapshot(m) for m in window])
    logger.info("历史翻页完成: %s 条消息", len(merged))
    return merged
