import json
import logging
import sqlite3
import subprocess
import sys
import os
import time
from contextlib import closing
from typing import List, Optional, Dict

from fastapi import APIRouter, HTTPException

from .db import DB_PATH
from .models import (
    Friend, GroupItem, SendMessagePayload, SendHistoryItem,
    ScheduleMessagePayload, ScheduledJobItem,
    GroupCreate, GroupUpdate, FriendGroupUpdate,
    QAItem, QACreateUpdate, AITestPayload, AITestResponse,
    AISettingsResponse, AISettingsUpdate,
    ReplyDraftCreate, ReplyDraftItem, ReplyDraftApprove,
    PersonaBuildPayload, StickerCaptureRequest,
)
from . import persona, stickers, sticker_capture, wx_history
from .ai_qa import answer_question, generate_reply_candidates
from .wechat import WeChatSingleton

logger = logging.getLogger(__name__)

router = APIRouter()

# 自动回复运行状态（使用独立子进程便于停止）
auto_reply_proc: Optional[subprocess.Popen] = None
auto_reply_running: bool = False


# 好友列表
@router.get('/friends', response_model=List[Friend])
def get_friends():
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            rows = cur.execute(
                """
                SELECT f.id, f.name, f.msg, f.uid, f.created_at, f.updated_at,
                       f.group_id, g.name AS group_name
                FROM friends f
                LEFT JOIN groups g ON f.group_id = g.id
                ORDER BY f.id DESC
                """
            ).fetchall()
            friends: List[Friend] = []
            for r in rows:
                try:
                    msg_obj = json.loads(r["msg"]) if r["msg"] else {}
                except Exception:
                    msg_obj = {}

                gid_raw = r["group_id"]
                gid: Optional[int] = None
                if gid_raw not in (None, ""):
                    try:
                        gid = int(gid_raw)
                    except Exception:
                        gid = None

                friends.append(Friend(
                    id=r["id"],
                    name=r["name"],
                    uid=r["uid"],
                    local=msg_obj.get("地区", ""),
                    phone=msg_obj.get("电话", ""),
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    group_id=gid,
                    group_name=r["group_name"] if r["group_name"] else None
                ))
            return friends
    except Exception as e:
        logger.exception("查询好友列表失败")
        return []


# 分组列表
@router.get('/groups', response_model=List[GroupItem])
def list_groups():
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            rows = cur.execute(
                """
                SELECT g.id, g.name, g.created_at, g.updated_at,
                       (SELECT COUNT(*) FROM friends f WHERE f.group_id = g.id) AS friends_count
                FROM groups g
                ORDER BY g.id DESC
                """
            ).fetchall()
            return [
                GroupItem(
                    id=r['id'],
                    name=r['name'],
                    created_at=r['created_at'],
                    updated_at=r['updated_at'],
                    friends_count=r['friends_count']
                ) for r in rows
            ]
    except Exception as e:
        logger.exception("查询分组失败")
        return []


@router.post('/groups')
def create_group(payload: GroupCreate):
    name = (payload.name or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail='分组名称不能为空')
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO groups(name) VALUES (?)", (name,))
            conn.commit()
            return {"success": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='分组名称已存在')
    except Exception as e:
        logger.exception("创建分组失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/groups/{group_id}')
def update_group(group_id: int, payload: GroupUpdate):
    name = (payload.name or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail='分组名称不能为空')
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='分组不存在')
            conn.commit()
            return {"success": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='分组名称已存在')
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新分组失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/groups/{group_id}')
def delete_group(group_id: int):
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cnt = cur.execute("SELECT COUNT(*) FROM friends WHERE group_id=?", (group_id,)).fetchone()[0]
            if cnt and cnt > 0:
                raise HTTPException(status_code=400, detail='分组下仍有好友，禁止删除')
            cur.execute("DELETE FROM groups WHERE id=?", (group_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='分组不存在')
            conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除分组失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/friends/{friend_id}/group')
def update_friend_group(friend_id: int, payload: FriendGroupUpdate):
    gid = payload.group_id
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            if gid is not None:
                g = cur.execute("SELECT id FROM groups WHERE id=?", (gid,)).fetchone()
                if not g:
                    raise HTTPException(status_code=404, detail='分组不存在')
            cur.execute("UPDATE friends SET group_id=? WHERE id=?", (gid, friend_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='好友不存在')
            conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新好友分组失败")
        raise HTTPException(status_code=500, detail=str(e))


# 发送即时消息
@router.post('/send_message')
def api_send_message(payload: SendMessagePayload):
    try:
        friend_ids = payload.friendIds or []
        friend_names = payload.friendNames or {}
        content = (payload.content or '').strip()
        group_names = payload.groups or []

        if not friend_ids or not content:
            raise HTTPException(status_code=400, detail='缺少必要参数')

        wx = WeChatSingleton.get_instance()
        if not wx:
            raise HTTPException(status_code=500, detail='微信实例获取失败')

        success_count = 0
        failures = []
        for friend_id in friend_ids:
            try:
                name = friend_names.get(str(friend_id)) or friend_names.get(friend_id) or f'未知用户({friend_id})'
                wx.SendMsg(content, name)
                success_count += 1
            except Exception as e:
                failures.append({str(friend_id): str(e)})

        result = {
            'success': True,
            'total': len(friend_ids),
            'successCount': success_count,
            'failures': failures
        }
        logger.info(f"即时发送完成，总计 {len(friend_ids)}，成功 {success_count}，失败 {len(failures)}")

        # 写历史
        try:
            with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO send_history(content, groups, friend_ids, total, success_count) VALUES (?, ?, ?, ?, ?)",
                    (
                        content,
                        json.dumps(group_names, ensure_ascii=False),
                        json.dumps(friend_ids, ensure_ascii=False),
                        len(friend_ids),
                        success_count,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"写入发送历史失败: {e}")

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发送消息错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# 历史记录
@router.get('/send_history', response_model=List[SendHistoryItem])
def list_send_history():
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, content, groups, friend_ids, total, success_count, created_at FROM send_history ORDER BY id DESC"
            ).fetchall()
            items: List[SendHistoryItem] = []
            for r in rows:
                try:
                    groups = json.loads(r['groups']) if r['groups'] else []
                except Exception:
                    groups = []
                try:
                    fids = json.loads(r['friend_ids']) if r['friend_ids'] else []
                except Exception:
                    fids = []
                items.append(SendHistoryItem(
                    id=r['id'],
                    content=r['content'],
                    groups=groups,
                    friend_ids=fids,
                    total=r['total'],
                    success_count=r['success_count'],
                    created_at=r['created_at'],
                ))
            return items
    except Exception as e:
        logger.exception("查询发送历史失败")
        return []


# 创建定时任务
@router.post('/schedule_message')
def create_schedule_message(payload: ScheduleMessagePayload):
    content = (payload.content or '').strip()
    group_ids = payload.groupIds or []
    run_at = (payload.runAt or '').strip()
    if not content:
        raise HTTPException(status_code=400, detail='内容不能为空')
    if not group_ids:
        raise HTTPException(status_code=400, detail='请选择分组')
    if not run_at:
        raise HTTPException(status_code=400, detail='请选择执行时间')
    try:
        from datetime import datetime
        run_at_db = run_at
        try:
            dt = datetime.strptime(run_at, '%Y-%m-%dT%H:%M')
            if dt <= datetime.now():
                raise HTTPException(status_code=400, detail='执行时间需晚于当前时间')
            run_at_db = dt.strftime('%Y-%m-%d %H:%M:%S')
        except HTTPException:
            raise
        except Exception:
            run_at_db = run_at.replace('T', ' ')

        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ",".join(["?"]*len(group_ids))
            g_rows = cur.execute(
                f"SELECT name FROM groups WHERE id IN ({placeholders})",
                tuple(group_ids)
            ).fetchall() if group_ids else []
            group_names = [gr['name'] for gr in g_rows]

            cur.execute(
                "INSERT INTO scheduled_jobs(content, groups, group_ids, run_at, status) VALUES (?, ?, ?, ?, 'pending')",
                (
                    content,
                    json.dumps(group_names, ensure_ascii=False),
                    json.dumps(group_ids, ensure_ascii=False),
                    run_at_db,
                ),
            )
            conn.commit()
            job_id = cur.execute("SELECT last_insert_rowid() AS id").fetchone()[0]
            return {"success": True, "id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建定时任务失败")
        raise HTTPException(status_code=500, detail=str(e))


# 查询定时任务
@router.get('/scheduled_jobs', response_model=List[ScheduledJobItem])
def list_scheduled_jobs():
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, content, groups, group_ids, run_at, status, total, success_count, error, created_at, updated_at FROM scheduled_jobs ORDER BY id DESC"
            ).fetchall()
            items: List[ScheduledJobItem] = []
            for r in rows:
                try:
                    groups = json.loads(r['groups']) if r['groups'] else []
                except Exception:
                    groups = []
                try:
                    gids = json.loads(r['group_ids']) if r['group_ids'] else []
                except Exception:
                    gids = []
                items.append(ScheduledJobItem(
                    id=r['id'],
                    content=r['content'],
                    groups=groups,
                    group_ids=gids,
                    run_at=r['run_at'],
                    status=r['status'],
                    total=r['total'],
                    success_count=r['success_count'],
                    error=r['error'] if r['error'] else None,
                    created_at=r['created_at'],
                    updated_at=r['updated_at'],
                ))
            return items
    except Exception as e:
        logger.exception("查询定时任务失败")
        return []


# 删除未执行的定时任务
@router.delete('/scheduled_jobs/{job_id}')
def delete_scheduled_job(job_id: int):
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT status FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail='定时任务不存在')
            status = row[0]
            if status != 'pending':
                raise HTTPException(status_code=400, detail='仅允许删除未执行的定时任务')
            cur.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
            conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除定时任务失败")
        raise HTTPException(status_code=500, detail=str(e))


# 知识库：分页列表
@router.get('/qa_kb')
def list_qa_kb(offset: int = 0, limit: int = 10):
    try:
        if limit <= 0:
            limit = 10
        if offset < 0:
            offset = 0
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            total = cur.execute("SELECT COUNT(*) AS c FROM qa_kb").fetchone()[0]
            rows = cur.execute(
                "SELECT id, question, answer, created_at, updated_at FROM qa_kb ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            items = [
                QAItem(
                    id=r['id'],
                    question=r['question'],
                    answer=r['answer'],
                    created_at=r['created_at'],
                    updated_at=r['updated_at']
                ).dict()
                for r in rows
            ]
            return {"items": items, "total": total}
    except Exception as e:
        logger.exception("查询知识库失败")
        return {"items": [], "total": 0}


# 知识库：新增
@router.post('/qa_kb')
def create_qa_kb(payload: QACreateUpdate):
    q = (payload.question or '').strip()
    a = (payload.answer or '').strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail='问题与答复均不能为空')
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO qa_kb(question, answer) VALUES (?, ?)", (q, a))
            conn.commit()
            rid = cur.execute("SELECT last_insert_rowid() AS id").fetchone()[0]
            return {"success": True, "id": rid}
    except Exception as e:
        logger.exception("新增知识库失败")
        raise HTTPException(status_code=500, detail=str(e))


# 知识库：更新
@router.put('/qa_kb/{rid}')
def update_qa_kb(rid: int, payload: QACreateUpdate):
    q = (payload.question or '').strip()
    a = (payload.answer or '').strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail='问题与答复均不能为空')
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE qa_kb SET question=?, answer=?, updated_at=DATETIME('now','localtime') WHERE id=?", (q, a, rid))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='条目不存在')
            conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新知识库失败")
        raise HTTPException(status_code=500, detail=str(e))


# 知识库：删除
@router.delete('/qa_kb/{rid}')
def delete_qa_kb(rid: int):
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM qa_kb WHERE id=?", (rid,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='条目不存在')
            conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除知识库失败")
        raise HTTPException(status_code=500, detail=str(e))


# 系统提示词：读取
@router.get('/ai_settings', response_model=AISettingsResponse)
def get_ai_settings():
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute(
                "SELECT system_prompt, target_contact, auto_reply_mode, "
                "persona_enabled, sticker_enabled, updated_at "
                "FROM ai_settings ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                return AISettingsResponse(
                    system=row['system_prompt'],
                    target_contact=row['target_contact'] or '',
                    auto_reply_mode=row['auto_reply_mode'] or 'auto',
                    persona_enabled=bool(row['persona_enabled']),
                    sticker_enabled=bool(row['sticker_enabled']),
                    updated_at=row['updated_at'],
                )
            # 兜底：无记录时返回空字符串
            return AISettingsResponse(system="", target_contact="", auto_reply_mode="draft")
    except Exception as e:
        logger.exception("读取系统提示词失败")
        raise HTTPException(status_code=500, detail=str(e))


# 系统提示词：更新
@router.put('/ai_settings')
def update_ai_settings(payload: AISettingsUpdate):
    sys = (payload.system or '').strip()
    if not sys:
        raise HTTPException(status_code=400, detail='系统提示词不能为空')
    target_contact = (payload.target_contact or '').strip()
    auto_reply_mode = (payload.auto_reply_mode or 'draft').strip().lower()
    if auto_reply_mode not in {'auto', 'draft'}:
        raise HTTPException(status_code=400, detail='自动回复模式无效')
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ai_settings SET system_prompt=?, target_contact=?, auto_reply_mode=?, "
                "persona_enabled=COALESCE(?, persona_enabled), sticker_enabled=COALESCE(?, sticker_enabled), "
                "updated_at=DATETIME('now','localtime') WHERE id=(SELECT id FROM ai_settings ORDER BY id DESC LIMIT 1)",
                (sys, target_contact, auto_reply_mode,
                 None if payload.persona_enabled is None else int(payload.persona_enabled),
                 None if payload.sticker_enabled is None else int(payload.sticker_enabled))
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO ai_settings(system_prompt, target_contact, auto_reply_mode, "
                    "persona_enabled, sticker_enabled) VALUES (?, ?, ?, ?, ?)",
                    (sys, target_contact, auto_reply_mode,
                     int(payload.persona_enabled is not False), int(bool(payload.sticker_enabled))),
                )
            conn.commit()
            return {"success": True}
    except Exception as e:
        logger.exception("更新系统提示词失败")
        raise HTTPException(status_code=500, detail=str(e))


# AI 测试：调用大模型 + 本地知识库进行智能答复
@router.post('/ai_test', response_model=AITestResponse)
def ai_test(payload: AITestPayload):
    q = (payload.question or '').strip()
    if not q:
        raise HTTPException(status_code=400, detail='请输入问题')
    try:
        res = answer_question(q, payload.system, payload.history)
        return AITestResponse(
            answer=res.get('answer', ''),
            matched_id=res.get('matched_id'),
            matched_question=res.get('matched_question')
        )
    except Exception as e:
        logger.exception("AI 测试失败")
        raise HTTPException(status_code=500, detail=str(e))


# ---- 待审核回复 ----------------------------------------------------------

_DRAFT_COLUMNS = (
    "id, chat_name, incoming_message, options_json, stickers_json, "
    "status, selected_index, created_at"
)


def _load_json_list(raw) -> list:
    try:
        value = json.loads(raw) if raw else []
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _draft_from_row(row) -> ReplyDraftItem:
    options = [str(o) for o in _load_json_list(row['options_json'])]
    picked = [s if isinstance(s, str) and s else None for s in _load_json_list(row['stickers_json'])]
    picked = (picked + [None] * len(options))[:len(options)]
    return ReplyDraftItem(
        id=row['id'],
        chat_name=row['chat_name'],
        incoming_message=row['incoming_message'],
        options=options,
        stickers=picked,
        status=row['status'],
        selected_index=row['selected_index'],
        created_at=row['created_at'],
    )


def _feature_flags() -> tuple:
    """读取 (风格分析开关, 表情包开关)。"""
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            row = conn.execute(
                "SELECT persona_enabled, sticker_enabled FROM ai_settings ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            return bool(row[0]), bool(row[1])
    except Exception:
        logger.exception("读取功能开关失败，使用默认值")
    return True, False


def _recent_drafts_have_sticker(conn: sqlite3.Connection, chat_name: str) -> bool:
    rows = conn.execute(
        "SELECT stickers_json FROM reply_drafts WHERE chat_name=? ORDER BY id DESC LIMIT 2",
        (chat_name,),
    ).fetchall()
    return any(any(_load_json_list(r[0])) for r in rows)


def _assign_stickers(chat_name: str, candidates: List[Dict]) -> List[Optional[str]]:
    """为候选挑选表情包：优先用模型标注的情绪，缺失时用关键词兜底（带冷却，避免每条都发）。"""
    picked = [stickers.pick_sticker(c.get('emotion')) for c in candidates]
    if any(picked):
        return picked
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
        if _recent_drafts_have_sticker(conn, chat_name):
            return picked
    return [stickers.pick_sticker(stickers.detect_emotion(c['text'])) for c in candidates]


# 待审核回复：列出候选草稿
@router.get('/reply-drafts', response_model=List[ReplyDraftItem])
def list_reply_drafts(status: str = 'pending', limit: int = 30):
    if status not in {'pending', 'approved', 'rejected', 'all'}:
        raise HTTPException(status_code=400, detail='状态参数无效')
    limit = max(1, min(limit, 100))
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            if status == 'all':
                rows = conn.execute(
                    f"SELECT {_DRAFT_COLUMNS} FROM reply_drafts ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_DRAFT_COLUMNS} FROM reply_drafts "
                    "WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            return [_draft_from_row(row) for row in rows]
    except Exception as e:
        logger.exception("读取待审核回复失败")
        raise HTTPException(status_code=500, detail=str(e))


# 收到消息后生成候选回复，但不发送
@router.post('/reply-drafts', response_model=ReplyDraftItem)
def create_reply_draft(payload: ReplyDraftCreate):
    chat_name = (payload.chat_name or '').strip()
    incoming_message = (payload.incoming_message or '').strip()
    if not chat_name or not incoming_message:
        raise HTTPException(status_code=400, detail='聊天对象和消息不能为空')
    try:
        # 先查重再调用模型。重复回调很常见，不能为了已经存在的草稿
        # 再执行一次昂贵的本地模型推理。
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                f"SELECT {_DRAFT_COLUMNS} FROM reply_drafts "
                "WHERE chat_name=? AND incoming_message=? AND status='pending' "
                "ORDER BY id DESC LIMIT 1",
                (chat_name, incoming_message),
            ).fetchone()
            if existing:
                return _draft_from_row(existing)

        use_persona, use_stickers = _feature_flags()
        candidates = generate_reply_candidates(
            incoming_message,
            history=payload.history,
            count=3,
            chat_name=chat_name,
            use_persona=use_persona,
            use_stickers=use_stickers,
        )
        if not candidates:
            raise HTTPException(status_code=502, detail='模型没有生成候选回复')
        options = [c['text'] for c in candidates]
        picked = _assign_stickers(chat_name, candidates) if use_stickers else [None] * len(options)

        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO reply_drafts(chat_name, incoming_message, options_json, stickers_json) "
                "VALUES (?, ?, ?, ?)",
                (chat_name, incoming_message,
                 json.dumps(options, ensure_ascii=False),
                 json.dumps(picked, ensure_ascii=False)),
            )
            conn.commit()
            row = conn.execute(
                f"SELECT {_DRAFT_COLUMNS} FROM reply_drafts WHERE rowid=?",
                (cur.lastrowid,),
            ).fetchone()
            return _draft_from_row(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("生成待审核回复失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/reply-drafts/{draft_id}/approve')
def approve_reply_draft(draft_id: int, payload: ReplyDraftApprove):
    """人工选择候选后，发送到指定微信聊天窗口（可附带表情包）。"""
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT {_DRAFT_COLUMNS} FROM reply_drafts WHERE id=?",
                (draft_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail='回复草稿不存在')
            if row['status'] != 'pending':
                raise HTTPException(status_code=409, detail='该草稿已经处理')
            draft = _draft_from_row(row)
            if not (0 <= payload.option_index < len(draft.options)):
                raise HTTPException(status_code=400, detail='候选回复编号无效')
            content = draft.options[payload.option_index].strip()
            if not content:
                raise HTTPException(status_code=400, detail='候选回复为空')
            sticker_rel = draft.stickers[payload.option_index] if payload.send_sticker else None
            sticker_file = stickers.resolve_sticker(sticker_rel) if sticker_rel else None

            wx = WeChatSingleton.get_instance()
            if not wx:
                raise HTTPException(
                    status_code=503,
                    detail='微信未连接：请确认电脑版微信已登录，并使用兼容的微信 4.1 客户端',
                )
            try:
                wx.SendMsg(content, draft.chat_name)
            except Exception as send_error:
                logger.exception("发送审核通过的回复失败")
                raise HTTPException(status_code=502, detail=f'微信发送失败：{send_error}')

            message = '已批准并发送'
            if sticker_rel and not sticker_file:
                message = '文字已发送，表情包文件不存在或已被移除'
            elif sticker_file:
                try:
                    time.sleep(0.8)  # 与文字消息拉开间隔，避免微信输入框来不及处理
                    wx.SendFiles(str(sticker_file), draft.chat_name)
                    message = '已批准并发送（含表情包）'
                except Exception as sticker_error:
                    logger.exception("发送表情包失败")
                    message = f'文字已发送，表情包发送失败：{sticker_error}'

            conn.execute(
                "UPDATE reply_drafts SET status='approved', selected_index=?, "
                "updated_at=DATETIME('now','localtime'), sent_at=DATETIME('now','localtime') "
                "WHERE id=?",
                (payload.option_index, draft_id),
            )
            conn.commit()
            return {'success': True, 'message': message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("批准回复草稿失败")
        raise HTTPException(status_code=500, detail=str(e))


# ---- 聊天记录风格分析 ------------------------------------------------------

@router.post('/persona/build')
def build_persona(payload: PersonaBuildPayload):
    """从微信读取与该联系人的历史聊天，保存语料并生成风格画像。"""
    chat_name = (payload.chat_name or '').strip()
    if not chat_name:
        raise HTTPException(status_code=400, detail='请先填写聊天对象')
    wx = WeChatSingleton.get_instance()
    if not wx:
        raise HTTPException(status_code=503, detail='微信未连接：请确认电脑版微信已登录')
    # 翻历史会滚动微信窗口，监听进程同时操作 UIAutomation 会互相干扰，先暂停再恢复
    was_running = bool(auto_reply_status().get('running'))
    try:
        if was_running:
            stop_auto_reply()
        wx.ChatWith(chat_name)
        time.sleep(1)
        wx_history.scroll_to_bottom(wx.native)
        messages = wx_history.collect_history(
            wx.native, max_scrolls=max(1, min(payload.load_more_times, 50)) * 4
        )
        rows = persona.extract_rows(messages)
        if not rows:
            raise HTTPException(status_code=404, detail='没有读取到文本消息，请确认聊天对象名称正确')
        saved = persona.save_corpus(chat_name, rows)
        profile = persona.refresh_profile(chat_name)
        return {
            'success': True,
            'read': len(rows),
            'corpus_total': saved['total'],
            'note': saved['reason'],
            'reliable': profile.get('reliable', False),
            'self_count': profile.get('self_count', 0),
            'summary': persona.render_profile(chat_name, profile),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("构建风格画像失败")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if was_running:
            try:
                start_auto_reply()
            except Exception:
                logger.exception("风格分析结束后恢复自动回复失败，请手动重新启动")


@router.get('/persona')
def get_persona(chat_name: str):
    """查看某个聊天对象已有的风格画像。"""
    profile = persona.load_profile(chat_name)
    if not profile:
        return {'exists': False}
    return {
        'exists': True,
        'reliable': profile.get('reliable', False),
        'self_count': profile.get('self_count', 0),
        'other_count': profile.get('other_count', 0),
        'updated_at': profile.get('updated_at'),
        'summary': persona.render_profile(chat_name, profile),
    }


@router.post('/stickers/capture')
def capture_stickers(payload: StickerCaptureRequest):
    """从微信聊天窗口里截取表情包，保存到素材库的 inbox 目录，等待归类。"""
    chat_name = (payload.chat_name or '').strip()
    if not chat_name:
        raise HTTPException(status_code=400, detail='请先填写聊天对象')
    wx = WeChatSingleton.get_instance()
    if not wx:
        raise HTTPException(status_code=503, detail='微信未连接：请确认电脑版微信已登录')
    # 截屏和滚动都要独占微信窗口，先暂停监听进程，结束后恢复
    was_running = bool(auto_reply_status().get('running'))
    try:
        if was_running:
            stop_auto_reply()
        wx.ChatWith(chat_name)
        time.sleep(1)
        wx_history.scroll_to_bottom(wx.native)
        result = sticker_capture.capture_stickers(
            wx.native,
            stickers.STICKER_DIR / stickers.INBOX_DIR,
            max_scrolls=max(1, min(payload.max_scrolls, 60)),
            only_self=payload.only_self,
        )
        return {
            'success': True,
            'saved': len(result['saved']),
            'duplicates': result['skipped_duplicate'],
            'seen': result['seen'],
            'inbox': str(stickers.STICKER_DIR / stickers.INBOX_DIR),
            'files': [os.path.basename(path) for path in result['saved']],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("截取表情包失败")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if was_running:
            try:
                start_auto_reply()
            except Exception:
                logger.exception("截取表情包结束后恢复自动回复失败，请手动重新启动")


# ---- 表情包库 --------------------------------------------------------------

@router.get('/stickers')
def get_sticker_library():
    return {
        'directory': str(stickers.STICKER_DIR),
        'library': stickers.list_library(),
        'emotions': [
            {'emotion': key, 'label': label}
            for key, (label, _) in stickers.EMOTIONS.items()
        ],
    }


@router.post('/reply-drafts/{draft_id}/reject')
def reject_reply_draft(draft_id: int):
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE reply_drafts SET status='rejected', updated_at=DATETIME('now','localtime') "
                "WHERE id=? AND status='pending'",
                (draft_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='待审核回复不存在或已经处理')
            conn.commit()
            return {'success': True, 'message': '已忽略该回复'}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("拒绝回复草稿失败")
        raise HTTPException(status_code=500, detail=str(e))


# 启动自动回复
@router.post('/api/start-auto-reply')
def start_auto_reply():
    """启动自动回复后台进程"""
    global auto_reply_proc, auto_reply_running

    try:
        if auto_reply_running:
            raise HTTPException(status_code=400, detail='自动回复已在运行中')

        # 先做连接预检，避免微信未授权时页面误报“已启动”。
        if not WeChatSingleton.get_instance():
            raise HTTPException(
                status_code=503,
                    detail='微信未连接：请确认电脑版微信已登录，并使用兼容的微信 4.1 客户端',
            )

        # 使用独立 Python 进程运行监听脚本，便于可靠停止
        script_path = os.path.join(os.path.dirname(__file__), 'listen_new_message.py')
        if not os.path.exists(script_path):
            raise HTTPException(status_code=404, detail='监听脚本不存在')

        # 监听进程会调用 Windows UIAutomation；降低其优先级，避免在解析
        # 微信控件或等待模型时影响桌面、浏览器和微信本身的响应。
        creationflags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        auto_reply_proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=os.path.dirname(__file__),
            creationflags=creationflags,
        )
        auto_reply_running = True
        logger.info("自动回复已启动")
        return {'success': True, 'message': '自动回复已启动'}
    except HTTPException:
        raise
    except Exception as e:
        auto_reply_running = False
        logger.error(f"启动自动回复失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/api/wechat-status')
def wechat_status():
    """返回当前后端是否能初始化免费版 wxauto4 微信连接。"""
    try:
        connected = WeChatSingleton.get_instance() is not None
        error = WeChatSingleton.get_last_error()
        return {
            'connected': connected,
            'message': '微信已连接' if connected else (error or '微信未连接，请确认微信版本与桌面登录状态'),
        }
    except BaseException as e:
        logger.exception("检查微信连接状态失败")
        return {'connected': False, 'message': f'微信连接检查失败：{e}'}


@router.get('/api/auto-reply-status')
def auto_reply_status():
    """返回自动回复监听子进程的真实状态，避免页面刷新后状态丢失。"""
    global auto_reply_proc, auto_reply_running
    if auto_reply_proc is not None and auto_reply_proc.poll() is not None:
        auto_reply_proc = None
        auto_reply_running = False
    running = bool(auto_reply_running and auto_reply_proc is not None and auto_reply_proc.poll() is None)
    auto_reply_running = running
    return {'running': running}

# 停止自动回复
@router.post('/api/stop-auto-reply')
def stop_auto_reply():
    """停止自动回复后台进程"""
    global auto_reply_proc, auto_reply_running

    try:
        if not auto_reply_running:
            raise HTTPException(status_code=400, detail='自动回复未在运行')

        if auto_reply_proc and auto_reply_proc.poll() is None:
            auto_reply_proc.terminate()
            auto_reply_proc.wait(timeout=10)
        auto_reply_proc = None
        auto_reply_running = False
        logger.info("自动回复已停止")
        return {'success': True, 'message': '自动回复已停止'}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"停止自动回复失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 修改好友备注
@router.post('/api/update-friend-remark')
def update_friend_remark(payload: dict):
    """修改好友备注"""
    try:
        friend_id = payload.get('friendId')
        new_remark = payload.get('newRemark', '').strip()

        # 验证参数
        if not friend_id or not new_remark:
            raise HTTPException(status_code=400, detail='缺少必要参数')

        # 从数据库获取好友信息
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM friends WHERE id=?", (friend_id,))
            friend = cursor.fetchone()

            if not friend:
                raise HTTPException(status_code=404, detail='好友不存在')

            friend_name = friend[0]

            # 获取微信实例
            wx = WeChatSingleton.get_instance()
            if not wx:
                raise HTTPException(status_code=500, detail='微信实例获取失败')

            # 免费版 wxauto4 不提供修改好友备注接口，明确返回不可用。
            try:
                if not hasattr(wx, 'ManageFriend'):
                    raise HTTPException(status_code=501, detail='免费版 wxauto4 暂不支持修改好友备注')
                # 先打开聊天窗口
                wx.ChatWith(friend_name)
                time.sleep(1.5)
                # 修改备注
                result = wx.ManageFriend(remark=new_remark)
                
                if not result:
                    raise HTTPException(status_code=500, detail='修改备注失败')
                
                logger.info(f"修改好友备注成功: 好友ID={friend_id}, 原名称={friend_name}, 新备注={new_remark}")

                # 更新数据库中的好友信息
                cursor.execute(
                    "UPDATE friends SET name=? WHERE id=?",
                    (new_remark, friend_id)
                )
                conn.commit()

                return {
                    'success': True,
                    'message': '修改备注成功',
                    'newName': new_remark
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"修改好友备注异常: {str(e)}")
                raise HTTPException(status_code=500, detail=f'修改备注过程中发生异常: {str(e)}')

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"修改好友备注错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# 获取聊天历史记录
# 获取聊天记录
@router.post('/api/get-chat-history')
def get_chat_history(payload: dict):
    """获取好友聊天记录并保存到数据库"""
    try:
        friend_id = payload.get('friendId')
        friend_name = payload.get('friendName')
        load_more = payload.get('loadMore', False)
        
        if not friend_id or not friend_name:
            raise HTTPException(status_code=400, detail='缺少必要参数')
        
        # 获取微信实例
        wx = WeChatSingleton.get_instance()
        if not wx:
            raise HTTPException(status_code=500, detail='微信实例获取失败')
        
        # 使用 wxauto4 获取历史记录
        try:
            # 先切换到该好友的聊天窗口
            wx.ChatWith(friend_name)
            time.sleep(1)
            
            # 如果是加载更多，使用免费版对应的缓存加载方法
            if load_more:
                try:
                    if hasattr(wx, 'LoadMoreMessage'):
                        wx.LoadMoreMessage()
                    elif hasattr(wx, 'LoadMoreCache'):
                        wx.LoadMoreCache()
                    time.sleep(1)
                except Exception as load_error:
                    logger.warning(f"加载更多消息失败: {str(load_error)}")
            
            # 获取所有消息
            messages = wx.GetAllMessage()
            
            # 格式化消息并保存到数据库
            history = []
            with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
                cursor = conn.cursor()
                
                if messages and isinstance(messages, list):
                    for msg in messages:
                        sender = getattr(msg, 'sender', '未知')
                        content = getattr(msg, 'content', '')
                        msg_time = getattr(msg, 'time', '')
                        msg_type = getattr(msg, 'type', '')
                        
                        history.append({
                            'sender': sender,
                            'content': content,
                            'time': msg_time,
                            'type': msg_type
                        })
                        
                        # 保存到数据库（去重：检查是否已存在相同消息）
                        try:
                            cursor.execute(
                                """INSERT INTO chat_history (friend_id, friend_name, sender, content, msg_type, msg_time)
                                   SELECT ?, ?, ?, ?, ?, ?
                                   WHERE NOT EXISTS (
                                       SELECT 1 FROM chat_history 
                                       WHERE friend_id = ? AND sender = ? AND content = ? AND msg_time = ?
                                   )""",
                                (friend_id, friend_name, sender, content, msg_type, msg_time,
                                 friend_id, sender, content, msg_time)
                            )
                        except Exception as db_err:
                            logger.warning(f"保存消息到数据库失败: {db_err}")
                
                conn.commit()
            
            logger.info(f"获取聊天历史成功: 好友={friend_name}, 消息数量={len(history)}")
            
            return {
                'success': True,
                'history': history,
                'total': len(history)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取聊天历史异常: {str(e)}")
            raise HTTPException(status_code=500, detail=f'获取历史记录过程中发生异常: {str(e)}')
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取聊天历史错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
