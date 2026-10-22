"""微信 AI 助手桌面小宠物。

小宠物通过现有 FastAPI 接口读取待审核草稿，点击候选按钮后直接发送到微信。
不需要打开前端网页；网页仍可用于完整管理功能。
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
import urllib.error
import urllib.request
from typing import Any, Callable


BACKEND = "http://127.0.0.1:8000"
DEFAULT_PROMPT = (
    "你是我的微信聊天助手，请模仿我的聊天风格与表达习惯。"
    "语气自然、亲密、口语化，优先使用短句，不要像客服或公告。"
    "不编造事实，不替我做金钱、隐私、承诺或重要决定。"
    "遇到敏感话题或无法确定的内容，回复简短并提醒我人工确认。"
)


class ApiClient:
    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
    ) -> Any:
        body = None
        headers = {"Connection": "close"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            BACKEND + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                if response.status >= 400:
                    raise RuntimeError(str(data.get("detail") or f"请求失败({response.status})"))
                return data
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8", errors="replace")).get("detail")
            except Exception:
                detail = None
            raise RuntimeError(str(detail or f"请求失败({error.code})")) from error
        except urllib.error.URLError as error:
            raise RuntimeError("后端尚未连接") from error


class DesktopPet:
    def __init__(self) -> None:
        self.api = ApiClient()
        self.root = tk.Tk()
        self.root.title("微信 AI 小宠物")
        self.root.resizable(False, False)
        self.root.configure(bg="#FFF8EF")
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.system_prompt = DEFAULT_PROMPT
        self.auto_reply_on = False
        self.start_requested = False
        self.busy = False
        self.editing_target = False
        self.current_draft: dict[str, Any] | None = None
        self._drag_origin: tuple[int, int] | None = None
        self.target_var = tk.StringVar()
        self.saved_target = ""
        self.persona_var = tk.BooleanVar(value=True)
        self.sticker_var = tk.BooleanVar(value=False)
        self.text_only_var = tk.BooleanVar(value=False)
        self.persona_busy = False
        self.import_busy = False
        self.status_var = tk.StringVar(value="正在连接后端…")
        self.detail_var = tk.StringVar(value="我会参考最近对话再想回复")
        self._build_ui()
        self._place_window()
        self.root.after(200, self.load_settings)
        self.root.after(1200, self.poll_status)
        self.root.after(1600, self.poll_drafts)

    def _build_ui(self) -> None:
        shell = tk.Frame(
            self.root,
            bg="#FFF8EF",
            highlightbackground="#F2C9A5",
            highlightthickness=2,
        )
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg="#FFE7D0", height=34)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<Button-1>", self._drag_start)
        header.bind("<B1-Motion>", self._drag_move)
        title_label = tk.Label(
            header,
            text="  🐰  微信 AI 小宠物",
            bg="#FFE7D0",
            fg="#6B3A25",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        title_label.pack(side="left", padx=4)
        title_label.bind("<Button-1>", self._drag_start)
        title_label.bind("<B1-Motion>", self._drag_move)
        close_button = tk.Button(
            header,
            text="×",
            command=self.close,
            relief="flat",
            bd=0,
            bg="#FFE7D0",
            activebackground="#F7C7A7",
            fg="#8B5E4A",
            font=("Arial", 16),
            width=2,
        )
        close_button.pack(side="right", padx=2)

        body = tk.Frame(shell, bg="#FFF8EF", padx=14, pady=10)
        body.pack(fill="both", expand=True)
        self.pet_label = tk.Label(body, text="🐰", bg="#FFF8EF", font=("Segoe UI Emoji", 42))
        self.pet_label.pack(pady=(0, 2))
        self.pet_label.bind("<Button-1>", self._drag_start)
        self.pet_label.bind("<B1-Motion>", self._drag_move)
        tk.Label(
            body,
            textvariable=self.status_var,
            bg="#FFF8EF",
            fg="#6B3A25",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack()
        tk.Label(
            body,
            textvariable=self.detail_var,
            bg="#FFF8EF",
            fg="#9A7564",
            font=("Microsoft YaHei UI", 9),
        ).pack(pady=(2, 7))

        settings = tk.Frame(body, bg="#FFF8EF")
        settings.pack(fill="x", pady=(0, 8))
        tk.Label(settings, text="目标联系人", bg="#FFF8EF", fg="#765445", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.target_entry = tk.Entry(
            settings,
            textvariable=self.target_var,
            relief="solid",
            bd=1,
            bg="white",
            fg="#4A342A",
            font=("Microsoft YaHei UI", 9),
            width=17,
        )
        self.target_entry.pack(side="left", padx=6, fill="x", expand=True)
        self.target_entry.bind("<FocusIn>", self._target_focus_in)
        self.target_entry.bind("<FocusOut>", self._target_focus_out)
        self.save_button = tk.Button(
            settings,
            text="保存",
            command=self.save_settings,
            relief="flat",
            bd=0,
            bg="#E99A6B",
            activebackground="#D98255",
            fg="white",
            font=("Microsoft YaHei UI", 9),
            padx=8,
        )
        self.save_button.pack(side="right")

        features = tk.Frame(body, bg="#FFF8EF")
        features.pack(fill="x", pady=(0, 8))
        check_style = dict(
            bg="#FFF8EF",
            activebackground="#FFF8EF",
            fg="#765445",
            selectcolor="white",
            font=("Microsoft YaHei UI", 9),
        )
        tk.Checkbutton(
            features, text="参考聊天风格", variable=self.persona_var, command=self.save_flags, **check_style
        ).pack(side="left")
        tk.Checkbutton(
            features, text="表情包", variable=self.sticker_var, command=self.save_flags, **check_style
        ).pack(side="left", padx=(6, 0))
        self.persona_button = tk.Button(
            features,
            text="分析聊天记录",
            command=self.build_persona,
            relief="flat",
            bd=0,
            bg="#E8D8CE",
            activebackground="#D8C4B8",
            fg="#6C5247",
            font=("Microsoft YaHei UI", 9),
            padx=8,
        )
        self.persona_button.pack(side="right")

        sticker_row = tk.Frame(body, bg="#FFF8EF")
        sticker_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            sticker_row,
            text="把聊天里你发过的表情存进素材库",
            bg="#FFF8EF",
            fg="#9A7564",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        self.import_button = tk.Button(
            sticker_row,
            text="导入表情包",
            command=self.import_stickers,
            relief="flat",
            bd=0,
            bg="#E8D8CE",
            activebackground="#D8C4B8",
            fg="#6C5247",
            font=("Microsoft YaHei UI", 9),
            padx=8,
        )
        self.import_button.pack(side="right")

        self.draft_frame = tk.Frame(body, bg="#FFF8EF")
        self.incoming_label = tk.Label(
            self.draft_frame,
            text="",
            justify="left",
            anchor="w",
            wraplength=310,
            bg="#FFF0E4",
            fg="#654438",
            padx=9,
            pady=7,
            font=("Microsoft YaHei UI", 9),
        )
        self.incoming_label.pack(fill="x", pady=(0, 7))
        self.text_only_check = tk.Checkbutton(
            self.draft_frame,
            text="只发文字，不带表情包",
            variable=self.text_only_var,
            bg="#FFF8EF",
            activebackground="#FFF8EF",
            fg="#9A7564",
            selectcolor="white",
            font=("Microsoft YaHei UI", 8),
        )
        self.options_frame = tk.Frame(self.draft_frame, bg="#FFF8EF")
        self.options_frame.pack(fill="x")
        self.reject_button = tk.Button(
            self.draft_frame,
            text="先忽略",
            command=self.reject_draft,
            relief="flat",
            bd=0,
            bg="#E8D8CE",
            activebackground="#D8C4B8",
            fg="#6C5247",
            font=("Microsoft YaHei UI", 9),
            padx=10,
        )
        self.reject_button.pack(pady=(7, 0), anchor="e")
        self.draft_frame.pack_forget()

        self.footer = tk.Label(
            body,
            text="点击候选后会直接发送到微信",
            bg="#FFF8EF",
            fg="#B28A76",
            font=("Microsoft YaHei UI", 8),
        )
        self.footer.pack(pady=(8, 0))

    def _place_window(self) -> None:
        self.root.update_idletasks()
        width, height = 360, 345
        x = max(0, self.root.winfo_screenwidth() - width - 28)
        y = max(0, self.root.winfo_screenheight() - height - 62)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        ox, oy = self._drag_origin
        self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def _target_focus_in(self, _event: tk.Event) -> None:
        if self.editing_target:
            return
        self.editing_target = True
        self.status_var.set("正在编辑联系人")
        self.detail_var.set("编辑期间暂时暂停微信监听，避免抢走输入焦点")
        # 无论页面状态是否刚刷新过，都主动请求停止，避免监听进程继续
        # 调用 Windows UIAutomation 抢走输入框焦点。
        self._run_async(
            lambda: self.api.request("/api/stop-auto-reply", "POST"),
            self._on_edit_listener_stopped,
        )

    def _target_focus_out(self, _event: tk.Event) -> None:
        self.editing_target = False
        if self.target_var.get().strip():
            # 给保存按钮或其他控件留出点击时间，再恢复微信监听。
            self.root.after(800, self.start_auto_reply)

    def _on_edit_listener_stopped(self, _data: Any, _error: Exception | None) -> None:
        self.auto_reply_on = False
        self.start_requested = False
        if self.editing_target:
            self.status_var.set("正在编辑联系人")
            self.detail_var.set("编辑期间暂时暂停微信监听，避免抢走输入焦点")

    def _run_async(self, work: Callable[[], Any], done: Callable[[Any, Exception | None], None]) -> None:
        def worker() -> None:
            try:
                result = work()
                error = None
            except Exception as exc:  # 网络和后端错误显示在宠物状态中
                result = None
                error = exc
            try:
                self.root.after(0, lambda: done(result, error))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def load_settings(self) -> None:
        self._run_async(lambda: self.api.request("/ai_settings"), self._on_settings_loaded)

    def _on_settings_loaded(self, data: Any, error: Exception | None) -> None:
        if error:
            self.status_var.set("等待后端连接…")
            self.detail_var.set(str(error))
            self.root.after(3000, self.load_settings)
            return
        self.system_prompt = str(data.get("system") or DEFAULT_PROMPT)
        self.saved_target = str(data.get("target_contact") or "")
        self.target_var.set(self.saved_target)
        self.persona_var.set(data.get("persona_enabled") is not False)
        self.sticker_var.set(bool(data.get("sticker_enabled")))
        if self.target_var.get().strip():
            self.status_var.set("我在守着消息")
            self.detail_var.set("有新消息时，我会参考最近对话再想几个回复")
            self.start_auto_reply()
        else:
            self.status_var.set("请填写目标联系人")
            self.detail_var.set("填写微信昵称或备注名后点击保存")

    def save_settings(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            self.status_var.set("还没有填写联系人")
            self.detail_var.set("请输入微信昵称或备注名")
            return
        self.save_button.configure(state="disabled")
        payload = {
            "system": self.system_prompt or DEFAULT_PROMPT,
            "target_contact": target,
            "auto_reply_mode": "draft",
            "persona_enabled": self.persona_var.get(),
            "sticker_enabled": self.sticker_var.get(),
        }
        self.saved_target = target
        self._run_async(lambda: self.api.request("/ai_settings", "PUT", payload), self._on_settings_saved)

    def _on_settings_saved(self, _data: Any, error: Exception | None) -> None:
        self.save_button.configure(state="normal")
        if error:
            self.status_var.set("保存失败")
            self.detail_var.set(str(error))
            return
        self.status_var.set("设置已保存")
        self.detail_var.set("正在重新连接消息监听")
        self._run_async(lambda: self.api.request("/api/stop-auto-reply", "POST"), self._restart_after_save)

    def _restart_after_save(self, _data: Any, _error: Exception | None) -> None:
        self.auto_reply_on = False
        self.start_requested = False
        self.start_auto_reply()

    def save_flags(self) -> None:
        """勾选“参考聊天风格/表情包”后立即保存，不重启监听。"""
        if not self.saved_target:
            self.status_var.set("请先填写并保存联系人")
            self.detail_var.set("保存联系人后，开关才会生效")
            return
        payload = {
            "system": self.system_prompt or DEFAULT_PROMPT,
            "target_contact": self.saved_target,
            "auto_reply_mode": "draft",
            "persona_enabled": self.persona_var.get(),
            "sticker_enabled": self.sticker_var.get(),
        }
        self._run_async(lambda: self.api.request("/ai_settings", "PUT", payload), self._on_flags_saved)

    def _on_flags_saved(self, _data: Any, error: Exception | None) -> None:
        if error:
            self.status_var.set("开关保存失败")
            self.detail_var.set(str(error))
            return
        self.detail_var.set(
            "表情包已开启，需要先在 backend/stickers/ 放图片" if self.sticker_var.get() else "设置已更新"
        )

    def build_persona(self) -> None:
        target = (self.saved_target or self.target_var.get()).replace("，", ",").split(",")[0].strip()
        if not target:
            self.status_var.set("请先填写目标联系人")
            return
        if self.persona_busy:
            return
        self.persona_busy = True
        self.persona_button.configure(state="disabled", text="分析中…")
        self.status_var.set("正在读取聊天记录")
        self.detail_var.set("会操作微信窗口，请先不要动鼠标键盘")
        # 读取历史会占用微信界面，先暂停监听，结束后再恢复。
        self.auto_reply_on = False
        self.start_requested = True

        def work() -> Any:
            try:
                self.api.request("/api/stop-auto-reply", "POST")
            except Exception:
                pass
            return self.api.request(
                "/persona/build", "POST", {"chat_name": target, "load_more_times": 10}, timeout=180
            )

        self._run_async(work, self._on_persona_built)

    def import_stickers(self) -> None:
        target = (self.saved_target or self.target_var.get()).replace("，", ",").split(",")[0].strip()
        if not target:
            self.status_var.set("请先填写目标联系人")
            return
        if self.import_busy or self.persona_busy:
            return
        self.import_busy = True
        self.import_button.configure(state="disabled", text="导入中…")
        self.status_var.set("正在截取表情包")
        self.detail_var.set("会滚动并截取微信窗口，请先不要动鼠标键盘，也别遮挡微信")
        self.auto_reply_on = False
        self.start_requested = True

        def work() -> Any:
            return self.api.request(
                "/stickers/capture",
                "POST",
                {"chat_name": target, "max_scrolls": 12, "only_self": True},
                timeout=240,
            )

        self._run_async(work, self._on_stickers_imported)

    def _on_stickers_imported(self, data: Any, error: Exception | None) -> None:
        self.import_busy = False
        self.start_requested = False
        self.import_button.configure(state="normal", text="导入表情包")
        if error:
            self.status_var.set("导入失败")
            self.detail_var.set(str(error))
        else:
            self.status_var.set(f"新增 {data.get('saved', 0)} 张表情包")
            self.detail_var.set("已放进 backend/stickers/inbox，按情绪移到对应文件夹后才会使用")
        self.start_auto_reply()

    def _on_persona_built(self, data: Any, error: Exception | None) -> None:
        self.persona_busy = False
        self.start_requested = False
        self.persona_button.configure(state="normal", text="分析聊天记录")
        if error:
            self.status_var.set("分析失败")
            self.detail_var.set(str(error))
        elif data.get("reliable"):
            self.status_var.set("风格分析完成")
            self.detail_var.set(f"读取 {data.get('read')} 条，你发送了 {data.get('self_count')} 条")
        else:
            self.status_var.set("样本偏少，暂不启用")
            self.detail_var.set(f"你只发送了 {data.get('self_count')} 条，可多翻聊天记录后重试")
        self.start_auto_reply()

    def start_auto_reply(self) -> None:
        if self.editing_target or not self.target_var.get().strip() or self.start_requested:
            return
        self.start_requested = True
        self._run_async(lambda: self.api.request("/api/start-auto-reply", "POST"), self._on_auto_started)

    def _on_auto_started(self, _data: Any, error: Exception | None) -> None:
        self.start_requested = False
        if error and "运行中" not in str(error):
            self.status_var.set("监听启动失败")
            self.detail_var.set(str(error))
            self.root.after(5000, self.poll_status)
            return
        self.auto_reply_on = True
        self.status_var.set("我在守着消息")
        self.detail_var.set("收到消息后，我会先思考")

    def poll_status(self) -> None:
        self._run_async(lambda: self.api.request("/api/auto-reply-status"), self._on_status)

    def _on_status(self, data: Any, error: Exception | None) -> None:
        if error:
            self.status_var.set("后端连接中…")
            self.root.after(4000, self.poll_status)
            return
        self.auto_reply_on = bool(data.get("running"))
        if not self.editing_target and not self.auto_reply_on and self.target_var.get().strip() and not self.current_draft:
            self.start_auto_reply()
        self.root.after(4000, self.poll_status)

    def poll_drafts(self) -> None:
        self._run_async(lambda: self.api.request("/reply-drafts?status=pending&limit=10"), self._on_drafts)

    def _on_drafts(self, data: Any, error: Exception | None) -> None:
        if error:
            self.root.after(2500, self.poll_drafts)
            return
        drafts = data if isinstance(data, list) else []
        if self.current_draft is not None:
            current_id = self.current_draft.get("id")
            if not any(item.get("id") == current_id for item in drafts):
                self.current_draft = None
                self._hide_draft()
        if self.current_draft is None and drafts:
            self.current_draft = drafts[0]
            self._show_draft(self.current_draft)
        self.root.after(1000, self.poll_drafts)

    def _show_draft(self, draft: dict[str, Any]) -> None:
        self.pet_label.configure(text="💭")
        self.status_var.set("我想好了，选一个回复")
        self.detail_var.set("点击下面任意一条，就会发送给对方")
        self.incoming_label.configure(text=f"{draft.get('chat_name', '对方')}：{draft.get('incoming_message', '')}")
        for child in self.options_frame.winfo_children():
            child.destroy()
        options = draft.get("options") or []
        stickers = draft.get("stickers") or []
        self.text_only_var.set(False)
        if any(stickers):
            self.text_only_check.pack(anchor="w", before=self.options_frame)
        else:
            self.text_only_check.pack_forget()
        for index, option in enumerate(options):
            sticker_mark = "   🖼 +表情包" if index < len(stickers) and stickers[index] else ""
            button = tk.Button(
                self.options_frame,
                text=f"{index + 1}. {option}{sticker_mark}",
                command=lambda selected=index: self.approve_draft(selected),
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                wraplength=310,
                bg="#FFE1C9",
                activebackground="#FFC89F",
                fg="#603B2B",
                font=("Microsoft YaHei UI", 9),
                padx=9,
                pady=6,
            )
            button.pack(fill="x", pady=2)
        self.draft_frame.pack(fill="x")
        # 按内容撑高，避免长回复或表情包提示被截掉；不超过屏幕可用高度
        self.root.update_idletasks()
        needed = self.root.winfo_children()[0].winfo_reqheight() + 6
        height = max(345, min(needed, self.root.winfo_screenheight() - 90))
        # 窗口停在屏幕下方时，向上展开而不是伸出屏幕外
        y = max(0, min(self.root.winfo_y(), self.root.winfo_screenheight() - height - 50))
        self.root.geometry(f"360x{height}+{self.root.winfo_x()}+{y}")

    def _hide_draft(self) -> None:
        self.draft_frame.pack_forget()
        self.pet_label.configure(text="🐰")
        self.status_var.set("我在守着消息")
        self.detail_var.set("有新消息时，我会参考最近对话再想几个回复")
        self.root.geometry("360x345")

    def approve_draft(self, index: int) -> None:
        if self.busy or not self.current_draft:
            return
        self.busy = True
        self.status_var.set("正在发送…")
        draft_id = int(self.current_draft["id"])
        # Tk 变量只能在主线程读取，不能放进后台线程的 lambda 里
        payload = {"option_index": index, "send_sticker": not self.text_only_var.get()}
        self._run_async(
            lambda: self.api.request(f"/reply-drafts/{draft_id}/approve", "POST", payload, timeout=30),
            self._on_draft_action,
        )

    def reject_draft(self) -> None:
        if self.busy or not self.current_draft:
            return
        self.busy = True
        draft_id = int(self.current_draft["id"])
        self._run_async(
            lambda: self.api.request(f"/reply-drafts/{draft_id}/reject", "POST"),
            self._on_draft_action,
        )

    def _on_draft_action(self, data: Any, error: Exception | None) -> None:
        self.busy = False
        if error:
            self.status_var.set("操作失败")
            self.detail_var.set(str(error))
            return
        self.current_draft = None
        self._hide_draft()
        message = str(data.get("message") or "") if isinstance(data, dict) else ""
        if message and not message.startswith("已批准并发送") and message != "已忽略该回复":
            self.detail_var.set(message)

    def close(self) -> None:
        self.status_var.set("正在停止监听…")
        self._run_async(lambda: self.api.request("/api/stop-auto-reply", "POST"), lambda _data, _error: None)
        self.root.after(120, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopPet().run()
