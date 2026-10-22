import logging
import threading
import time
from typing import Optional


class WeChatSingleton:
    """
    微信发送器单例
    - 基于免费版 wxauto4.WeChat 封装发送能力
    - 初始化失败时返回 None，让上层友好提示
    """

    _instance: Optional["WeChatSingleton"] = None
    _last_error: str = ""
    _lock = threading.RLock()
    _last_attempt_at = 0.0
    _retry_interval_seconds = 10.0

    def __init__(self):
        self._ok = False
        self._wx = None
        try:
            # 尝试初始化 COM 环境（在某些机器上必须）
            try:
                import pythoncom  # type: ignore
                pythoncom.CoInitialize()
            except Exception:
                # 非致命，继续尝试初始化微信
                pass

            # 延迟导入，避免模块导入阶段失败
            # 免费版 wxauto4 不需要设备激活；项目已切换到该包。
            from wxauto4 import WeChat  # type: ignore
            try:
                # 发送器不需要监听消息，也不应改变微信窗口尺寸。
                self._wx = WeChat(start_listener=False, resize=False, ads=False)
            except TypeError:
                # 兼容不认识 ads 参数的旧版 wxauto4，仍尽量关闭自动缩放。
                try:
                    self._wx = WeChat(start_listener=False, resize=False)
                except TypeError:
                    self._wx = WeChat(start_listener=False)
            self._ok = True
            WeChatSingleton._last_error = ""
            logging.info("WeChat 初始化成功")
        except BaseException as e:
            logging.exception(f"WeChat 初始化失败: {e}")
            self._ok = False
            self._wx = None
            WeChatSingleton._last_error = str(e)

    @classmethod
    def get_instance(cls) -> Optional["WeChatSingleton"]:
        try:
            with cls._lock:
                # 微信可能在后端之后才启动；失败时不要让前端每几秒都重新
                # 扫描一次 UIAutomation 窗口，避免后台状态轮询造成卡顿。
                if cls._instance and cls._instance._ok:
                    return cls._instance
                now = time.monotonic()
                if now - cls._last_attempt_at < cls._retry_interval_seconds:
                    return None
                cls._last_attempt_at = now
                cls._instance = WeChatSingleton()
                if cls._instance and cls._instance._ok:
                    return cls._instance
                return None
        except BaseException:
            logging.exception("创建微信实例失败")
            return None

    @classmethod
    def get_last_error(cls) -> str:
        return cls._last_error

    @property
    def native(self):
        """底层 wxauto4.WeChat 对象，供需要直接操作控件的模块使用。"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        return self._wx

    def SendMsg(self, content: str, friend_name: str):
        """发送消息到指定好友备注/昵称。
        - 若未初始化，抛出异常供上层捕获
        """
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        # 调用真实发送逻辑
        self._wx.SendMsg(content, friend_name)
    
    def SendFiles(self, filepath: str, friend_name: str):
        """发送图片/文件（表情包）到指定好友。"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        self._wx.SendFiles(filepath, friend_name)

    def ChatWith(self, friend_name: str):
        """打开与指定好友的聊天窗口"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        return self._wx.ChatWith(friend_name)
    
    def ManageFriend(self, remark: str = None, **kwargs):
        """管理好友信息（修改备注等）"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        return self._wx.ManageFriend(remark=remark, **kwargs)
    
    def GetAllMessage(self):
        """获取当前聊天窗口的所有消息"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        return self._wx.GetAllMessage()
    
    def LoadMoreMessage(self):
        """加载更多历史消息"""
        if not self._ok or not self._wx:
            raise RuntimeError("微信未初始化或不可用")
        return self._wx.LoadMoreMessage()
