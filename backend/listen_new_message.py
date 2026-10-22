import time
import logging
import requests
import json
import os
import sqlite3
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from wxauto4 import WeChat

try:
    from wxauto4 import WxParam
except ImportError:
    WxParam = None

# ==================== 增强日志配置 ====================
# 确保日志目录存在
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)

# 创建日志文件名
log_filename = os.path.join(log_dir, f'wechat_ai_assistant_{datetime.now().strftime("%Y%m%d")}.log')

# 清除之前的日志配置（防止重复）
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

# 设置日志级别
logger = logging.getLogger('WeChatAIAssistant')
logger.setLevel(logging.INFO)

SETTINGS_DB_PATH = os.path.join(os.path.dirname(__file__), "wechat_friends.db")
HEARTBEAT_PATH = os.path.join(log_dir, "listener.heartbeat")
# 监听进程停了不到这么久再启动，视为“重启空档”，要把空档里收到的消息补上
RESTART_GAP_SECONDS = 180
# 补读时，已经为同一条消息生成过草稿的时间范围
RECENT_DRAFT_MINUTES = 10


def load_reply_settings():
    """读取自动回复运行时设置。监听进程独立运行，不能依赖 FastAPI 进程内存。"""
    defaults = {"target_contact": "", "auto_reply_mode": "draft"}
    try:
        with sqlite3.connect(SETTINGS_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT target_contact, auto_reply_mode FROM ai_settings "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                defaults["target_contact"] = (row["target_contact"] or "").strip()
                defaults["auto_reply_mode"] = (row["auto_reply_mode"] or "draft").strip().lower()
    except Exception as exc:
        logger.warning("读取自动回复设置失败: %s", exc)
    return defaults

# 测试日志是否工作
logger.info("=" * 50)
logger.info("🚀 微信AI助手日志系统启动")
logger.info(f"📁 日志文件路径: {log_filename}")
logger.info("=" * 50)

# ==================== AI助手核心类 ====================
class WeChatAIAssistant:
    def __init__(self, check_interval=2):
        # wxauto4 的默认构造参数会自动启动一个监听线程并调整微信窗口尺寸。
        # 监听进程只需要一个监听线程；关闭自动缩放可避免窗口不断抢占桌面和出现
        # 鼠标上下调整光标，同时降低 UIAutomation 对系统的压力。
        if WxParam is not None:
            WxParam.LISTEN_INTERVAL = max(2, int(check_interval))
            WxParam.LISTENER_EXCUTOR_WORKERS = 1
            # wxauto4 默认监听窗口高度为 4800 像素，会在部分桌面上形成
            # 超出屏幕的巨大子窗口。缩小到足够读取最近消息的尺寸，减少
            # UIAutomation 控件数量和窗口重绘。
            if hasattr(WxParam, "CHAT_WINDOW_SIZE"):
                WxParam.CHAT_WINDOW_SIZE = (1000, 900)
        try:
            self.wx = WeChat(start_listener=False, resize=False, ads=False)
        except TypeError:
            # 兼容不认识 ads 参数的旧版 wxauto4，仍尽量关闭自动缩放。
            try:
                self.wx = WeChat(start_listener=False, resize=False)
            except TypeError:
                self.wx = WeChat(start_listener=False)
        self.check_interval = check_interval
        self.current_chat = None
        self.conversation_history = {}  # 存储每个聊天的对话历史
        # 消息去重：改为 TTL 机制，允许相同内容在一段时间后再次处理
        self.processed_messages = {}
        self.dedup_ttl_seconds = 1.5
        self.seen_message_keys = {}
        self.seen_message_ttl_seconds = 3600
        self.latest_message_keys = {}
        self.message_control_ids = {}
        self._latest_reader_name = None
        # 消息列表是虚拟化的，只读轮询很便宜（约 2ms），可以高频进行
        self.poll_interval = 0.5
        # 对方连发多条时，安静这么久再合并成一次生成；最长等待不超过 burst_max_seconds
        self.quiet_seconds = 1.5
        self.burst_max_seconds = 8.0
        self.bursts = {}
        self.burst_lock = threading.Lock()
        self.gap_seconds = self._heartbeat_age()
        self._last_heartbeat_write = 0.0
        self.reply_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reply-draft")
        

        
    def start_listening(self):
        """启动智能微信监听"""
        logger.info("🚀 启动智能微信AI助手...")
        logger.info("📝 回复先生成候选，等待网页批准后发送")
        logger.info(f"⏰ 检查间隔: {self.check_interval}秒")
        settings = load_reply_settings()
        if settings["target_contact"]:
            logger.info("🎯 只自动回复联系人: %s", settings["target_contact"])
        else:
            logger.warning("⚠️ 尚未设置目标联系人，自动回复已安全停用")
        targets = self._split_targets(settings["target_contact"])
        if not targets:
            return
        try:
            listen_chats = []
            for target in targets:
                result = self.wx.AddListenChat(target, self._on_free_message)
                chat = result if hasattr(result, "GetAllMessage") else None
                if chat is None:
                    logger.warning("无法建立微信事件监听对象: %s (%s)", target, result)
                    continue
                # AddListenChat 创建子窗口时，wxauto4 已在 ChatBox 初始化阶段
                # 建立消息 ID 基线；这里不要再调用 GetAllMessage() 扫描历史。
                listen_chats.append((target, chat))
                logger.info("✅ 已添加微信监听对象: %s (%s, type=%s)", target, result, type(result).__name__)

            if not listen_chats:
                logger.error("❌ 没有成功建立微信监听对象，监听进程退出")
                return

            # AddListenChat 会启动 wxauto4 自带的低频监听线程。不要再调用
            # GetAllMessage() 轮询：该接口会完整扫描 UIAutomation 控件树，
            # 高频调用时会导致微信窗口和桌面卡顿。
            # 部分 wxauto4 构建版本不会在 AddListenChat 后自动拉起线程，
            # 因此显式调用一次；如果线程已经存在，StartListening 会直接返回。
            if hasattr(self.wx, "StartListening"):
                self.wx.StartListening()
            listener_thread = getattr(self.wx, "_listener_thread", None)
            if listener_thread is not None:
                logger.info("🔎 wxauto4 监听线程状态: alive=%s", listener_thread.is_alive())
            logger.info("✅ wxauto4 事件监听已启动; listening_chats=%s", len(listen_chats))
            # 41.1.7 的部分构建版本虽然线程存活，但不会稳定触发回调。
            # 用“最新一条消息”做低频兜底，避免重新高频扫描完整聊天记录。
            self._poll_latest_messages(listen_chats)
        except KeyboardInterrupt:
            logger.info("🛑 用户停止监听")
        except Exception as e:
            logger.error("❌ wxauto4 监听错误: %s", e, exc_info=True)

    @staticmethod
    def _split_targets(value):
        return [
            part.strip()
            for part in (value or "").replace("，", ",").replace("；", ",").replace(";", ",").split(",")
            if part.strip()
        ]

    def _message_key(self, msg, chat_name):
        message_id = getattr(msg, "id", None)
        if message_id not in (None, ""):
            return f"{chat_name}|id:{message_id}"
        hash_value = getattr(msg, "hash", "") or ""
        if callable(hash_value):
            hash_value = ""
        return "|".join([
            str(chat_name or ""),
            str(getattr(msg, "sender", "") or ""),
            str(getattr(msg, "content", "") or ""),
            str(getattr(msg, "attr", "") or ""),
            str(getattr(msg, "time", "") or ""),
            str(hash_value),
        ])

    def _claim_message(self, msg, chat_name):
        """为回调和轮询共用去重，避免一条消息生成多条候选。"""
        key = self._message_key(msg, chat_name)
        now = time.time()
        previous = self.seen_message_keys.get(key)
        if previous is not None and now - previous < self.seen_message_ttl_seconds:
            return False
        self.seen_message_keys[key] = now
        if len(self.seen_message_keys) > 2000:
            cutoff = now - self.seen_message_ttl_seconds
            self.seen_message_keys = {
                item_key: timestamp
                for item_key, timestamp in self.seen_message_keys.items()
                if timestamp >= cutoff
            }
        return True

    @staticmethod
    def _heartbeat_age():
        """上一次监听进程还活着是多久以前；从没有过记录返回 None。"""
        try:
            return max(0.0, time.time() - os.path.getmtime(HEARTBEAT_PATH))
        except OSError:
            return None

    def _write_heartbeat(self, force=False):
        now = time.time()
        if not force and now - self._last_heartbeat_write < 5:
            return
        self._last_heartbeat_write = now
        try:
            with open(HEARTBEAT_PATH, "w", encoding="utf-8") as handle:
                handle.write(str(now))
        except OSError:
            pass

    @staticmethod
    def _is_self_message(msg):
        sender = str(getattr(msg, "sender", "") or "").strip().casefold()
        attr = str(getattr(msg, "attr", "") or "").strip().casefold()
        return sender == "self" or attr == "self" or bool(getattr(msg, "is_self", False))

    def _already_drafted(self, chat_name, msg):
        """最近是否已经为这条消息生成过草稿（不论是否已处理）。"""
        content = str(getattr(msg, "content", "") or "").strip()
        if not content:
            return True
        try:
            with sqlite3.connect(SETTINGS_DB_PATH) as conn:
                row = conn.execute(
                    "SELECT 1 FROM reply_drafts WHERE chat_name=? "
                    "AND created_at >= DATETIME('now','localtime',?) "
                    "AND instr(incoming_message, ?) > 0 LIMIT 1",
                    (chat_name, f"-{RECENT_DRAFT_MINUTES} minutes", content),
                ).fetchone()
            return row is not None
        except Exception as exc:
            logger.warning("查询已有草稿失败: %s", exc)
            return False

    def _recover_after_gap(self, chat_name, controls, chat_api):
        """监听刚（重新）启动时调用：只在“刚重启”的情况下补读空档里收到的消息。

        以前启动时会把当前画面里的所有消息都当成历史丢掉，进程重启的几秒里
        对方发来的消息就丢了。现在如果上一个监听进程不久前还活着，就取末尾
        连续的、对方发的、还没生成过草稿的消息。冷启动（很久没运行）不回复旧历史。
        """
        gap = self.gap_seconds
        if gap is None or gap > RESTART_GAP_SECONDS:
            logger.info("🆕 冷启动，不处理画面里的历史消息: chat=%s", chat_name)
            return []
        from wxauto4.msgs.msg import parse_msg

        tail = []
        for control in controls[-8:]:
            try:
                tail.append(parse_msg(control, chat_api))
            except Exception as error:
                logger.debug("补读解析失败: %s", error)
        run = []
        for msg in reversed(tail):
            if self._is_self_message(msg):
                break
            if str(getattr(msg, "type", "") or "").casefold() != "text":
                continue
            run.append(msg)
        run.reverse()
        missed = [msg for msg in run if not self._already_drafted(chat_name, msg)]
        if missed:
            logger.info("🩹 补读重启空档里的消息: chat=%s count=%s gap=%.0fs", chat_name, len(missed), gap)
        return missed

    def _read_new_messages(self, chat, chat_name):
        """只解析上次检查后新增的消息控件，避免连续聊天漏消息。

        消息列表是虚拟化的，滚动或重排时靠近边缘的控件会被回收并换新的
        runtime id，只按“id 不在上次集合里”判断会把旧消息反复当成新消息。
        新消息总是追加在列表末尾，所以只取“最后一个已知控件”之后的部分。
        """
        api = getattr(chat, "_api", None)
        chat_api = getattr(api, "_chat_api", None)
        msgbox = getattr(chat_api, "msgbox", None)
        get_children = getattr(msgbox, "GetChildren", None) if msgbox is not None else None
        if callable(get_children):
            try:
                controls = get_children() or []
                ids = [str(getattr(control, "runtimeid", None)) for control in controls]
                previous = self.message_control_ids.get(chat_name)
                if self._latest_reader_name is None:
                    self._latest_reader_name = "msgbox-new"
                    logger.info("🔎 消息兜底读取方式: msgbox-new")
                if previous is not None and ids == previous:
                    return []
                self.message_control_ids[chat_name] = ids
                if not previous:
                    return self._recover_after_gap(chat_name, controls, chat_api)
                known = set(previous)
                last_known = max((index for index, rid in enumerate(ids) if rid in known), default=-1)
                new_controls = controls[last_known + 1:]
                if not new_controls:
                    return []
                from wxauto4.msgs.msg import parse_msg

                return [parse_msg(control, chat_api) for control in new_controls]
            except Exception as error:
                logger.debug("轻量消息控件读取失败: %s", error)

        return self._read_latest_message(chat)

    def _read_latest_message(self, chat):
        """兼容没有消息控件列表接口的旧构建版本。"""
        api = getattr(chat, "_api", None)
        chat_api = getattr(api, "_chat_api", None)

        for owner, method_name in (
            (chat_api, "get_last_msg"),
            (api, "get_last_msg"),
            (chat, "GetLastMessage"),
        ):
            method = getattr(owner, method_name, None) if owner is not None else None
            if callable(method):
                try:
                    message = method()
                except Exception as error:
                    logger.debug("轻量读取接口失败: %s (%s)", method_name, error)
                    continue
                if self._latest_reader_name is None:
                    self._latest_reader_name = method_name
                    logger.info("🔎 消息兜底读取方式: %s", method_name)
                return message

        # 极旧构建版本的最终兜底。外层会自动放慢频率，避免持续卡顿。
        messages = chat.GetAllMessage() if hasattr(chat, "GetAllMessage") else []
        if self._latest_reader_name is None:
            self._latest_reader_name = "GetAllMessage[-1]"
            logger.warning("⚠️ wxauto4 无最后消息接口，兜底使用低频 GetAllMessage")
        return messages[-1] if messages else None

    def _poll_latest_messages(self, listen_chats):
        """低频兜底检测最新消息，兼容回调未触发的 wxauto4 构建版本。"""
        while True:
            for target, chat in listen_chats:
                try:
                    messages = self._read_new_messages(chat, target)
                    if not isinstance(messages, list):
                        messages = [messages] if messages is not None else []
                    for message in messages:
                        key = self._message_key(message, target)
                        if key in self.latest_message_keys.get(target, set()):
                            continue
                        self.latest_message_keys.setdefault(target, set()).add(key)
                        if len(self.latest_message_keys[target]) > 200:
                            self.latest_message_keys[target] = set(list(self.latest_message_keys[target])[-100:])
                        logger.info(
                            "🔔 兜底发现新消息: chat=%s sender=%s content=%s",
                            target,
                            getattr(message, "sender", ""),
                            getattr(message, "content", ""),
                        )
                        self._on_free_message(message, chat, fallback_name=target)
                except Exception as error:
                    logger.warning("⚠️ 读取最新微信消息失败: chat=%s error=%s", target, error)
            self._flush_due_bursts()
            self._write_heartbeat()
            if self._latest_reader_name == "GetAllMessage[-1]":
                # 完整历史扫描只作为旧版本兼容路径，放慢频率避免卡顿
                time.sleep(max(6.0, float(self.check_interval)))
            else:
                # msgbox-new 只读控件列表并只解析新增控件，可以高频
                time.sleep(self.poll_interval)

    def _on_free_message(self, msg, chat, fallback_name=None):
        """wxauto4 回调：收到目标聊天的新消息后生成待审核草稿。"""
        try:
            chat_name = getattr(chat, "who", None)
            if not chat_name and hasattr(chat, "ChatInfo"):
                try:
                    chat_name = (chat.ChatInfo() or {}).get("chat_name")
                except Exception:
                    chat_name = None
            chat_name = str(chat_name or getattr(msg, "chat_name", "") or fallback_name or "").strip()
            if not chat_name:
                logger.warning("⚠️ 收到微信消息但无法识别聊天对象，跳过")
                return
            if not self._claim_message(msg, chat_name):
                return
            logger.info("🔔 wxauto4 收到消息: chat=%s sender=%s content=%s",
                        chat_name, getattr(msg, "sender", ""), getattr(msg, "content", ""))
            if not self.is_target_contact(chat_name, msg) or not self.is_valid_message(msg, chat_name):
                return
            self.conversation_history.setdefault(chat_name, [])
            self._queue_burst(chat_name, msg, chat)
        except Exception as e:
            logger.error("❌ wxauto4 消息回调处理失败: %s", e, exc_info=True)
    
    def _queue_burst(self, chat_name, msg, chat):
        """对方连发几条时先攒着，安静一会儿后合并成一次生成。"""
        now = time.time()
        with self.burst_lock:
            burst = self.bursts.get(chat_name)
            if burst is None:
                burst = {"contents": [], "first": now, "chat": chat, "msg": msg}
                self.bursts[chat_name] = burst
            content = str(getattr(msg, "content", "") or "").strip()
            if content:
                burst["contents"].append(content)
            burst["last"] = now
            burst["chat"] = chat
            burst["msg"] = msg

    def _flush_due_bursts(self):
        now = time.time()
        due = []
        with self.burst_lock:
            for chat_name, burst in list(self.bursts.items()):
                quiet = now - burst["last"] >= self.quiet_seconds
                too_old = now - burst["first"] >= self.burst_max_seconds
                if quiet or too_old:
                    due.append((chat_name, self.bursts.pop(chat_name)))
        for chat_name, burst in due:
            contents = burst["contents"]
            if not contents:
                continue
            last = burst["msg"]
            merged = SimpleNamespace(
                content="\n".join(contents),
                sender=getattr(last, "sender", ""),
                attr=getattr(last, "attr", ""),
                type="text",
                parts=list(contents),
            )
            if len(contents) > 1:
                logger.info("🧩 合并对方连发的 %s 条消息: chat=%s", len(contents), chat_name)
            # UIAutomation 和模型请求都放到独立工作线程，监听循环只负责发现消息。
            self.reply_executor.submit(self.process_reply_draft, chat_name, merged, burst["chat"])

    def handle_new_messages(self, messages):
        """处理新消息"""
        chat_name = messages.get('chat_name')
        msgs = messages.get('msg', [])
        # 兼容非列表返回
        if msgs and not isinstance(msgs, list):
            msgs = [msgs]
        if not msgs:
            logger.debug("📭 新消息容器为空，跳过处理")
            return
        # 尝试从第一条消息或当前上下文获取聊天名
        if not chat_name:
            try:
                sample = msgs[0]
                chat_name = getattr(sample, 'chat_name', None) or self.current_chat or '未知聊天'
            except Exception:
                chat_name = self.current_chat or '未知聊天'
        
        logger.info(f"📨 收到 {len(msgs)} 条新消息来自: {chat_name}")
        
        for msg in msgs:
            # 先记录原始消息信息（调试用）
            logger.debug(f"🔍 检查消息: sender={getattr(msg, 'sender', '')}, content={getattr(msg, 'content', '')[:20]}...")

            if not self.is_target_contact(chat_name, msg):
                logger.info("⏭️ 非目标联系人消息，跳过自动回复: chat=%s sender=%s", chat_name, getattr(msg, "sender", ""))
                continue
            
            if not self.is_valid_message(msg, chat_name):
                logger.info(f"⏭️  消息被过滤，不处理")
                continue
            
            # 详细记录消息
            logger.info(f"📱 消息详情:")
            logger.info(f"   💬 聊天: {chat_name}")
            logger.info(f"   👤 发送者: {msg.sender}")
            logger.info(f"   📝 内容: {msg.content}")
            logger.info(f"   🔖 类型: {msg.type}")
            logger.info(f"   🏷️ 属性: {msg.attr}")
            logger.info(f"   🔍 是否自己: self={getattr(msg, 'self', None)}, is_self={getattr(msg, 'is_self', None)}")
            
            # 如果是新聊天，初始化对话历史
            if chat_name not in self.conversation_history:
                self.conversation_history[chat_name] = []
                self.current_chat = chat_name
                logger.info(f"🆕 新聊天创建: {chat_name}")

            # 当前产品流程固定为候选审核：只生成候选，不直接发送。
            self.process_reply_draft(chat_name, msg)
    
    def process_intelligent_response(self, chat_name, msg):
        try:
            logger.info(f"🎯 开始处理回复: {chat_name}")
            
            # 初始化对话历史
            self.conversation_history.setdefault(chat_name, [])

            # 统一通过后端 /ai_test 接口生成回复，不在本地拼接
            # 添加到对话历史
            self.conversation_history[chat_name].append({
                "role": "user",
                "content": msg.content,
                "sender": msg.sender
            })
            
            # 保持对话历史在合理长度
            if len(self.conversation_history[chat_name]) > 20:
                self.conversation_history[chat_name] = self.conversation_history[chat_name][-20:]
                logger.debug("🧹 清理旧对话历史")
            
            # 获取AI回复（调用本地 /ai_test）
            ai_response = self.get_ai_response(chat_name, msg.content)

            if ai_response:
                # 发送回复（增强健壮性：重试与备用发送器）
                sent_ok = False
                try:
                    logger.info(f"📤 尝试发送回复到: {chat_name}")
                    self.wx.SendMsg(ai_response, who=chat_name)
                    sent_ok = True
                except Exception as e:
                    logger.warning(f"⚠️ 首次发送失败，尝试选择聊天后重试: {e}")
                    try:
                        # 若支持选择聊天窗口，先选择再发送
                        if hasattr(self.wx, 'SelectChat'):
                            try:
                                self.wx.SelectChat(chat_name)
                                logger.info(f"✅ 已选择聊天窗口: {chat_name}")
                            except Exception as se:
                                logger.debug(f"选择聊天窗口失败: {se}")
                        self.wx.SendMsg(ai_response, who=chat_name)
                        sent_ok = True
                    except Exception as e2:
                        logger.warning(f"⚠️ 第二次发送失败，尝试备用发送器: {e2}")
                        try:
                            # 备用发送器（通过单例封装）
                            from .wechat import WeChatSingleton  # 延迟导入避免循环依赖
                            wx_single = WeChatSingleton.get_instance()
                            if wx_single:
                                wx_single.SendMsg(ai_response, chat_name)
                                sent_ok = True
                            else:
                                logger.error("❌ 备用发送器不可用")
                        except Exception as e3:
                            logger.error(f"❌ 备用发送器发送失败: {e3}")

                if sent_ok:
                    # 添加到对话历史
                    self.conversation_history[chat_name].append({
                        "role": "assistant",
                        "content": ai_response,
                        "sender": "AI助手"
                    })
                    logger.info(f"🤖 AI回复成功:")
                    logger.info(f"   💬 回复内容: {ai_response}")
                    logger.info(f"   📊 对话历史长度: {len(self.conversation_history[chat_name])}")
                else:
                    logger.error(f"❌ AI回复发送失败: {chat_name}")
                
        except Exception as e:
            logger.error(f"❌ 回复处理错误: {e}", exc_info=True)
            self.wx.SendMsg("抱歉，我暂时无法回复，请稍后再试。", who=chat_name)

    def _collect_recent_history(self, chat, chat_name, current_msg):
        """读取当前聊天最近 10 条文本消息并转换为对话角色。"""
        if chat is None or not hasattr(chat, "GetAllMessage"):
            return []
        try:
            messages = chat.GetAllMessage() or []
        except Exception as error:
            logger.warning("读取最近聊天记录失败: %s", error)
            return []

        current_key = self._message_key(current_msg, chat_name)
        # 合并后的连发消息会作为“当前问题”单独传给模型，不要再混进上下文里
        burst_parts = set(getattr(current_msg, "parts", None) or [])
        history = []
        for item in messages[-20:]:
            if self._message_key(item, chat_name) == current_key:
                continue
            content = str(getattr(item, "content", "") or "").strip()
            if content in burst_parts and not self._is_self_message(item):
                continue
            msg_type = str(getattr(item, "type", "") or "").casefold()
            sender = str(getattr(item, "sender", "") or "").strip().casefold()
            attr = str(getattr(item, "attr", "") or "").strip().casefold()
            if not content or (msg_type and msg_type != "text"):
                continue
            if sender in {"system", "系统", "微信"} or attr in {"system", "系统"}:
                continue
            is_self = sender == "self" or attr == "self" or getattr(item, "is_self", False)
            history.append({
                "role": "assistant" if is_self else "user",
                "content": content[:1000],
            })
        result = history[-10:]
        logger.info("🧠 已读取聊天上下文: chat=%s messages=%s", chat_name, len(result))
        return result

    def process_reply_draft(self, chat_name, msg, chat=None):
        """生成候选回复并写入后端待审核列表，不直接触碰微信发送接口。"""
        try:
            self.conversation_history.setdefault(chat_name, [])
            recent_history = self._collect_recent_history(chat, chat_name, msg)
            if recent_history:
                self.conversation_history[chat_name] = recent_history
            previous_messages = self.conversation_history[chat_name][-10:]
            payload = {
                "chat_name": chat_name,
                "incoming_message": msg.content,
                "history": [
                    {"role": item.get("role"), "content": item.get("content")}
                    for item in previous_messages
                    if item.get("role") in {"user", "assistant"} and item.get("content")
                ],
            }
            response = requests.post(
                "http://127.0.0.1:8000/reply-drafts",
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            self.conversation_history[chat_name].append({
                "role": "user",
                "content": msg.content,
                "sender": msg.sender,
            })
            logger.info(
                "📝 已生成待审核回复: chat=%s draft_id=%s options=%s",
                chat_name,
                data.get("id"),
                len(data.get("options") or []),
            )
        except Exception as e:
            logger.error("❌ 生成待审核回复失败: %s", e, exc_info=True)
    
    def get_ai_response(self, chat_name, user_message):
        """调用后端本地接口获取智能回复（与前端问答测试一致）"""
        try:
            logger.info("🔄 调用本地AI测试接口...")
            previous_messages = self.conversation_history.get(chat_name, [])[:-1]
            payload = {
                "question": user_message,
                "history": [
                    {"role": item.get("role"), "content": item.get("content")}
                    for item in previous_messages[-12:]
                    if item.get("role") in {"user", "assistant"} and item.get("content")
                ],
            }
            # 通过本地 FastAPI 接口，避免外网证书问题

            response = requests.post("http://127.0.0.1:8000/ai_test", json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            content = (data.get('answer') or '').strip()
            cleaned_response = self.clean_response(content or "")
            logger.info(f"✅ AI调用成功，回复长度: {len(cleaned_response)}字符")
            return cleaned_response or "好的。"
        except Exception as e:
            logger.error(f"❌ AI调用失败: {e}", exc_info=True)
            return "我现在有点忙，稍后再回复你好吗？"

    def is_target_contact(self, chat_name, msg):
        """仅允许设置页指定的联系人触发自动回复。支持备注名或昵称精确匹配。"""
        settings = load_reply_settings()
        target_text = settings.get("target_contact", "")
        if not target_text:
            return False

        def name_variants(value):
            text = str(value or "").strip().casefold()
            if not text:
                return set()
            variants = {text}
            # 微信聊天标题可能包含备注补充信息，例如：
            # “联系人甲（备注）”；消息 sender 可能只有昵称。
            short_name = re.sub(r"[（(][^（）()]*[）)]$", "", text).strip()
            if short_name:
                variants.add(short_name)
            return variants

        # 支持用逗号、分号或换行配置多个联系人，仅匹配使用者自行保存的联系人。
        targets = set()
        for part in target_text.replace("，", ",").replace("；", ",").replace(";", ",").split(","):
            targets.update(name_variants(part))
        candidates = set()
        for value in (chat_name, getattr(msg, "sender", ""), getattr(msg, "chat_name", "")):
            candidates.update(name_variants(value))
        return bool(targets.intersection(candidates))
    
    def clean_response(self, response):
        """清理AI回复内容"""
        # 移除多余的空行和空格
        response = response.strip()
        # 限制长度
        if len(response) > 300:
            response = response[:297] + "..."
        return response
    
    
    def is_valid_message(self, msg, chat_name):
        """验证消息是否有效"""
        # 检查类型（兼容缺失或大小写差异）
        msg_type = getattr(msg, 'type', '') or ''
        if msg_type and msg_type.lower() != 'text':
            logger.debug(f"🚫 忽略非文本消息: {msg_type}")
            return False
        
        # 检查是否是自己发送的消息（避免回复自己的消息）
        sender = getattr(msg, 'sender', '')
        content = getattr(msg, 'content', '')
        msg_attr = getattr(msg, 'attr', '')
        sender_text = str(sender or '').strip().casefold()
        attr_text = str(msg_attr or '').strip().casefold()

        # 系统时间、撤回提示等消息不应触发 AI 生成，避免无意义的
        # 模型调用和 UIAutomation 回调堆积。
        if sender_text in {'system', '系统', '微信'} or attr_text in {'system', '系统'}:
            logger.debug(f"🚫 忽略系统消息: sender={sender}, attr={msg_attr}")
            return False
        
        # 关键修复：检查 sender 和 attr 的值是否是 'self'
        if sender_text == 'self' or attr_text == 'self' or getattr(msg, 'is_self', False):
            logger.info(f"🚫 忽略自己发送的消息: sender={sender}, attr={msg_attr}, content={content[:30]}...")
            return False
        
        # 如果 sender 为空，也可能是自己发的消息
        if not sender:
            logger.info(f"🚫 忽略sender为空的消息: {content[:30]}...")
            return False
            
        # 检查重复
        msg_key = f"{chat_name}_{sender}_{content}"
        now_ts = time.time()
        last_ts = self.processed_messages.get(msg_key)
        if last_ts is not None and (now_ts - last_ts) < self.dedup_ttl_seconds:
            logger.debug(f"🔄 忽略短期内重复消息: {msg_key}")
            return False

        # 记录本次处理时间
        self.processed_messages[msg_key] = now_ts

        # 清理旧的消息记录（防止内存溢出）
        if len(self.processed_messages) > 1000:
            logger.debug("🧹 清理消息记录缓存")
            # 只保留最近 TTL 内的记录，其他删除
            cutoff = now_ts - self.dedup_ttl_seconds
            self.processed_messages = {k: ts for k, ts in self.processed_messages.items() if ts >= cutoff}
            
        return True
    
    def get_chat_stats(self):
        """获取聊天统计"""
        stats = {
            "total_chats": len(self.conversation_history),
            "active_chats": list(self.conversation_history.keys()),
            "total_messages": sum(len(hist) for hist in self.conversation_history.values())
        }
        logger.info(f"📊 统计信息: {stats}")
        return stats

# ==================== 启动程序 ====================
if __name__ == "__main__":
    print("🤖 智能微信AI助手启动中...")
    print("📋 功能特性:")
    print("   ✓ 智能对话回复")
    print("   ✓ 上下文记忆")
    print("   ✓ 多聊天切换")
    print("   ✓ 详细日志记录")
    print("   ✓ 错误处理")
    print()
    print(f"📁 日志文件将保存在: {log_filename}")
    print()
    
    assistant = WeChatAIAssistant(check_interval=1.5)
    
    try:
        assistant.start_listening()
    except KeyboardInterrupt:
        logger.info("🛑 程序已安全停止")
        stats = assistant.get_chat_stats()
        logger.info(f"📊 运行统计: {stats}")
        print(f"📁 日志文件位置: {log_filename}")
