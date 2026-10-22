from typing import List, Optional, Dict
from pydantic import BaseModel


# 朋友条目（展示与基本信息）
class Friend(BaseModel):
    id: int
    name: str
    uid: str
    local: str = ""
    phone: str = ""
    created_at: str
    updated_at: str
    group_id: Optional[int] = None
    group_name: Optional[str] = None


# 分组条目（列表展示）
class GroupItem(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    friends_count: int = 0


# 创建分组
class GroupCreate(BaseModel):
    name: str


# 更新分组
class GroupUpdate(BaseModel):
    name: str


# 更新好友所属分组
class FriendGroupUpdate(BaseModel):
    group_id: Optional[int]


# 发送消息请求载荷
class SendMessagePayload(BaseModel):
    friendIds: List[int]
    friendNames: Dict[str, str]
    content: str
    groups: List[str] = []


# 发送历史条目
class SendHistoryItem(BaseModel):
    id: int
    content: str
    groups: List[str]
    friend_ids: List[int]
    total: int
    success_count: int
    created_at: str


# 创建定时任务载荷
class ScheduleMessagePayload(BaseModel):
    content: str
    groupIds: List[int]
    runAt: str


# 定时任务条目
class ScheduledJobItem(BaseModel):
    id: int
    content: str
    groups: List[str]
    group_ids: List[int]
    run_at: str
    status: str
    total: Optional[int] = None
    success_count: Optional[int] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


# 问答知识库条目
class QAItem(BaseModel):
    id: int
    question: str
    answer: str
    created_at: str
    updated_at: str


# 新增/更新知识库载荷
class QACreateUpdate(BaseModel):
    question: str
    answer: str


# AI 测试请求载荷
class AITestPayload(BaseModel):
    question: str
    system: Optional[str] = None
    history: List[Dict[str, str]] = []


# AI 测试响应
class AITestResponse(BaseModel):
    answer: str
    matched_id: Optional[int] = None
    matched_question: Optional[str] = None

# 系统提示词设置
class AISettingsResponse(BaseModel):
    system: str
    target_contact: str = ""
    auto_reply_mode: str = "draft"
    persona_enabled: bool = True
    sticker_enabled: bool = False
    updated_at: Optional[str] = None

class AISettingsUpdate(BaseModel):
    system: str
    target_contact: str = ""
    auto_reply_mode: str = "draft"
    # None 表示调用方没传（如桌面小宠物），保持数据库里的原值
    persona_enabled: Optional[bool] = None
    sticker_enabled: Optional[bool] = None


class ReplyDraftCreate(BaseModel):
    chat_name: str
    incoming_message: str
    history: List[Dict[str, str]] = []


class ReplyDraftItem(BaseModel):
    id: int
    chat_name: str
    incoming_message: str
    options: List[str]
    # 与 options 等长；每项是素材库内的相对路径，没有表情包则为 None
    stickers: List[Optional[str]] = []
    status: str
    selected_index: Optional[int] = None
    created_at: str


class ReplyDraftApprove(BaseModel):
    option_index: int
    # 为 False 时只发文字；默认附带该候选配的表情包
    send_sticker: bool = True


class PersonaBuildPayload(BaseModel):
    chat_name: str
    # 向上翻历史记录的次数，每次约加载几十条
    load_more_times: int = 10


class StickerCaptureRequest(BaseModel):
    chat_name: str
    # 从聊天底部向上滚动的次数，每次约 2 格滚轮
    max_scrolls: int = 12
    # True 只取自己发的，False 只取对方发的，None 都取
    only_self: Optional[bool] = None
