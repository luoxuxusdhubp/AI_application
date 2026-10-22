"use client";

import { useEffect, useMemo, useState } from "react";

type ReplyDraft = {
  id: number;
  chat_name: string;
  incoming_message: string;
  options: string[];
  stickers?: (string | null)[];
  status: string;
  created_at: string;
};

type StickerLibraryItem = { emotion: string; label: string; count: number };
type PersonaInfo = { exists: boolean; reliable?: boolean; self_count?: number; updated_at?: string; summary?: string };

export default function SystemSettings() {
  const backendBase = useMemo(() => "http://127.0.0.1:8000", []);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [targetContact, setTargetContact] = useState("");
  const replyMode = "draft" as const;
  const [drafts, setDrafts] = useState<ReplyDraft[]>([]);
  const [draftBusy, setDraftBusy] = useState<string | null>(null);
  // 聊天记录风格分析 / 表情包
  const [personaEnabled, setPersonaEnabled] = useState(true);
  const [stickerEnabled, setStickerEnabled] = useState(false);
  const [persona, setPersona] = useState<PersonaInfo | null>(null);
  const [personaBusy, setPersonaBusy] = useState(false);
  const [personaMsg, setPersonaMsg] = useState<string | null>(null);
  const [stickerDir, setStickerDir] = useState("");
  const [stickerLibrary, setStickerLibrary] = useState<StickerLibraryItem[]>([]);
  const [textOnly, setTextOnly] = useState<Record<number, boolean>>({});
  const [wechatConnected, setWechatConnected] = useState<boolean | null>(null);
  const [wechatStatus, setWechatStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 自动回复控制
  const [autoReplyOn, setAutoReplyOn] = useState(false);
  const [opMsg, setOpMsg] = useState<string | null>(null);
  const [opErr, setOpErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${backendBase}/ai_settings`);
        const data = await res.json();
        if (res.ok && typeof data?.system === "string") {
          setSystemPrompt(String(data.system || ""));
          setTargetContact(String(data.target_contact || ""));
          setPersonaEnabled(data.persona_enabled !== false);
          setStickerEnabled(Boolean(data.sticker_enabled));
        }
      } catch (e) {
        setError("加载系统提示词失败");
      }
    })();
  }, [backendBase]);

  const save = async () => {
    setSaving(true);
    setSavedMsg(null);
    setError(null);
    try {
      const sys = systemPrompt.trim();
      if (!sys) {
        setError("系统提示词不能为空");
        return;
      }
      const res = await fetch(`${backendBase}/ai_settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system: sys,
          target_contact: targetContact.trim(),
          auto_reply_mode: replyMode,
          persona_enabled: personaEnabled,
          sticker_enabled: stickerEnabled,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) {
        throw new Error(data?.detail || data?.error || `保存失败(${res.status})`);
      }
      setSavedMsg("已保存系统提示词");
    } catch (e: any) {
      setError(e?.message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const firstTarget = () => targetContact.split(/[,，;；]/)[0].trim();

  const loadStickers = async () => {
    try {
      const res = await fetch(`${backendBase}/stickers`);
      if (!res.ok) return;
      const data = await res.json();
      setStickerDir(String(data?.directory || ""));
      setStickerLibrary(Array.isArray(data?.library) ? data.library : []);
    } catch {
      // 后端未启动时忽略
    }
  };

  const loadPersona = async (name: string) => {
    if (!name) {
      setPersona(null);
      return;
    }
    try {
      const res = await fetch(`${backendBase}/persona?chat_name=${encodeURIComponent(name)}`);
      if (res.ok) setPersona(await res.json());
    } catch {
      // 后端未启动时忽略
    }
  };

  const buildPersona = async () => {
    const name = firstTarget();
    if (!name) {
      setPersonaMsg("请先在“自动回复对象”里填写联系人并保存");
      return;
    }
    setPersonaBusy(true);
    setPersonaMsg("正在读取微信聊天记录，请不要操作微信窗口…");
    try {
      const res = await fetch(`${backendBase}/persona/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_name: name, load_more_times: 10 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `分析失败(${res.status})`);
      setPersonaMsg(
        data.reliable
          ? `分析完成：读取 ${data.read} 条消息，其中你发送的 ${data.self_count} 条。`
          : `只有 ${data.self_count} 条你发送的消息，样本偏少，暂不注入风格画像；可多翻聊天记录后重试。`
      );
      await loadPersona(name);
    } catch (e: any) {
      setPersonaMsg(e?.message || "分析失败");
    } finally {
      setPersonaBusy(false);
    }
  };

  useEffect(() => {
    loadStickers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendBase]);

  const personaTarget = firstTarget();
  useEffect(() => {
    loadPersona(personaTarget);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendBase, personaTarget]);

  const fetchDrafts = async () => {
    try {
      const res = await fetch(`${backendBase}/reply-drafts?status=pending&limit=30`);
      if (!res.ok) return;
      const data = await res.json();
      setDrafts(Array.isArray(data) ? data : []);
    } catch {
      // 后端未启动时静默等待，页面其他设置仍可使用
    }
  };

  useEffect(() => {
    fetchDrafts();
    const timer = window.setInterval(fetchDrafts, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendBase]);

  useEffect(() => {
    const checkWechat = async () => {
      try {
        const res = await fetch(`${backendBase}/api/wechat-status`);
        const data = await res.json();
        setWechatConnected(Boolean(data?.connected));
        setWechatStatus(String(data?.message || ""));
      } catch {
        setWechatConnected(false);
        setWechatStatus("后端未连接");
      }
    };
    checkWechat();
    const timer = window.setInterval(checkWechat, 10000);
    return () => window.clearInterval(timer);
  }, [backendBase]);

  useEffect(() => {
    const checkAutoReply = async () => {
      try {
        const res = await fetch(`${backendBase}/api/auto-reply-status`);
        if (!res.ok) return;
        const data = await res.json();
        setAutoReplyOn(Boolean(data?.running));
      } catch {
        // 后端暂不可用时保留当前页面状态，避免覆盖其他设置操作。
      }
    };
    checkAutoReply();
    const timer = window.setInterval(checkAutoReply, 5000);
    return () => window.clearInterval(timer);
  }, [backendBase]);

  const approveDraft = async (draftId: number, optionIndex: number) => {
    const busyKey = `${draftId}-${optionIndex}`;
    setDraftBusy(busyKey);
    try {
      const res = await fetch(`${backendBase}/reply-drafts/${draftId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ option_index: optionIndex, send_sticker: !textOnly[draftId] }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `发送失败(${res.status})`);
      if (data?.message && !String(data.message).startsWith("已批准并发送")) {
        setOpErr(String(data.message));
      }
      setDrafts((prev) => prev.filter((draft) => draft.id !== draftId));
    } catch (e: any) {
      setOpErr(e?.message || "批准发送失败");
    } finally {
      setDraftBusy(null);
    }
  };

  const rejectDraft = async (draftId: number) => {
    setDraftBusy(`reject-${draftId}`);
    try {
      const res = await fetch(`${backendBase}/reply-drafts/${draftId}/reject`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `忽略失败(${res.status})`);
      setDrafts((prev) => prev.filter((draft) => draft.id !== draftId));
    } catch (e: any) {
      setOpErr(e?.message || "忽略失败");
    } finally {
      setDraftBusy(null);
    }
  };

  const startAutoReply = async () => {
    setStarting(true);
    setOpMsg(null);
    setOpErr(null);
    try {
      const res = await fetch(`${backendBase}/api/start-auto-reply`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) {
        throw new Error(data?.detail || data?.error || `启动失败(${res.status})`);
      }
      setAutoReplyOn(true);
      setOpMsg("已启动自动回复");
    } catch (e: any) {
      setOpErr(e?.message || "启动失败");
    } finally {
      setStarting(false);
    }
  };

  const stopAutoReply = async () => {
    setStopping(true);
    setOpMsg(null);
    setOpErr(null);
    try {
      const res = await fetch(`${backendBase}/api/stop-auto-reply`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) {
        throw new Error(data?.detail || data?.error || `停止失败(${res.status})`);
      }
      setAutoReplyOn(false);
      setOpMsg("已停止自动回复");
    } catch (e: any) {
      setOpErr(e?.message || "停止失败");
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <h1 className="text-2xl font-semibold text-gray-800">系统配置</h1>
      
      <div className="bg-white rounded-xl shadow-md p-6 border border-gray-100 space-y-6">
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900">系统提示词</h2>
        <p className="text-sm text-gray-600">用于指导 AI 回答风格与约束。自动回复时会使用这里的语气、人设和边界。</p>
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="例如：严格依据知识库作答，回答简洁礼貌；不足时提示需要更多信息。"
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          rows={6}
        />
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setSystemPrompt(
              "你是我的微信聊天助手，请模仿我的聊天风格与表达习惯。"
              + "\n语气自然、亲密、口语化，优先使用短句，不要像客服或公告。"
              + "\n可以根据上下文表达关心、回应情绪，适度使用情侣之间的称呼和语气词，但不要机械重复。"
              + "\n不编造我不知道的事实，不替我做金钱、承诺、隐私或重要决定。"
              + "\n遇到争吵、敏感话题或无法确定的内容，回复简短并提醒我人工确认。"
              + "\n如果对方直接询问是否由 AI 回复，要诚实说明并提醒我接手。"
            )}
            className="rounded-lg border border-primary-600 px-3 py-2 text-sm font-medium text-primary-700 hover:bg-primary-50"
          >填入情侣聊天模板</button>
          <span className="text-xs text-gray-500">模板可以继续按你的习惯修改</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={save}
            disabled={saving}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors duration-200 ${saving ? 'bg-gray-400 cursor-not-allowed' : 'bg-primary-600 hover:bg-primary-700 focus:ring-2 focus:ring-primary-500 focus:ring-offset-2'}`}
          >保存</button>
          {error && <span className="text-sm text-red-600">{error}</span>}
          {savedMsg && <span className="text-sm text-green-600">{savedMsg}</span>}
        </div>
      </div>

        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900">自动回复对象</h2>
          <p className="text-sm text-gray-600">
            只处理这里填写的微信昵称或备注名。留空时不会自动回复任何联系人，避免误发。
          </p>
          <input
            value={targetContact}
            onChange={(e) => setTargetContact(e.target.value)}
            placeholder="例如：小仙女（填写微信里的昵称或备注名）"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <div className="text-xs text-amber-700">
            建议先只填写一个联系人，并先用普通消息测试；不要在未确认风格前启动自动回复。
          </div>
        </div>

        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900">聊天记录风格分析</h2>
          <p className="text-sm text-gray-600">
            读取你和该联系人的历史聊天，统计你的说话习惯（句长、语气词、表情），并在回复时参考过去相似场景下你的真实回复。
          </p>
          <label className="flex items-center gap-2 text-sm text-gray-800">
            <input type="checkbox" checked={personaEnabled} onChange={(e) => setPersonaEnabled(e.target.checked)} />
            回复时参考聊天记录风格（修改后点上方“保存”生效）
          </label>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={buildPersona}
              disabled={personaBusy || wechatConnected !== true}
              className={`rounded-lg px-4 py-2 text-sm font-medium text-white ${personaBusy || wechatConnected !== true ? "bg-gray-400 cursor-not-allowed" : "bg-primary-600 hover:bg-primary-700"}`}
            >{personaBusy ? "分析中…" : "读取聊天记录并分析"}</button>
            {persona?.exists && (
              <span className="text-xs text-gray-500">
                上次分析：{persona.updated_at}（你发送的 {persona.self_count} 条）
              </span>
            )}
          </div>
          {personaMsg && <div className="text-sm text-gray-700">{personaMsg}</div>}
          {persona?.exists && persona.summary && (
            <pre className="whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-700">{persona.summary}</pre>
          )}
          <div className="text-xs text-amber-700">
            隐私提示：风格统计与少量相似的历史对话片段会随提示词发送给大模型服务（与最近 10 条上下文一样）。不希望发送时请取消勾选。
          </div>
        </div>

        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900">表情包</h2>
          <p className="text-sm text-gray-600">
            开启后，AI 会在合适的候选回复后配一张本地表情包；批准时随文字一起发送，也可选择只发文字。
          </p>
          <label className="flex items-center gap-2 text-sm text-gray-800">
            <input type="checkbox" checked={stickerEnabled} onChange={(e) => setStickerEnabled(e.target.checked)} />
            启用表情包（修改后点上方“保存”生效）
          </label>
          <div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-700 space-y-1">
            <div>素材库目录：<code>{stickerDir || "加载中…"}</code></div>
            <div>把图片（png/jpg/gif）放进以情绪命名的子文件夹，如 <code>happy</code> 或 <code>开心</code>、<code>love</code>/<code>爱你</code>、<code>sad</code>/<code>难过</code>。</div>
            <div>
              当前素材：
              {stickerLibrary.length === 0
                ? "还没有表情包"
                : stickerLibrary.map((item) => `${item.label} ${item.count} 张`).join("、")}
              <button type="button" onClick={loadStickers} className="ml-2 text-primary-700 underline">刷新</button>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <h2 className="text-lg font-semibold text-gray-900">微信连接状态</h2>
          <div className={`rounded-md border p-3 text-sm ${wechatConnected ? "border-green-200 bg-green-50 text-green-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
            {wechatConnected ? "✓ " : "⚠️ "}
            {wechatStatus || "正在检查微信连接…"}
          </div>
          {!wechatConnected && (
            <p className="text-xs text-gray-600">
              请确认电脑版微信已登录，并使用微信 4.1 系列客户端；免费版 wxauto4 不需要激活码。
              如仍无法连接，请重启微信和后端后刷新本页。
            </p>
          )}
        </div>

        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900">自动回复</h2>
        <p className="text-sm text-gray-600">建议使用“候选审核”模式：AI 只生成选项，点击批准后才会发送到微信。</p>
        <div className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-900">
          候选审核模式：AI 只生成多个选项，必须由你点击批准后才会发送。
        </div>
        <p className="text-xs text-amber-700">
          选择模式后点击上方“保存”。候选审核模式不会未经你同意发送消息。
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={startAutoReply}
            disabled={starting || autoReplyOn || wechatConnected !== true}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors duration-200 ${starting || autoReplyOn ? 'bg-gray-400 cursor-not-allowed' : 'bg-primary-600 hover:bg-primary-700 focus:ring-2 focus:ring-primary-500 focus:ring-offset-2'}`}
          >启动自动回复</button>
          <button
            onClick={stopAutoReply}
            disabled={stopping || !autoReplyOn}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors duration-200 ${stopping || !autoReplyOn ? 'bg-gray-400 cursor-not-allowed' : 'bg-red-600 hover:bg-red-700 focus:ring-2 focus:ring-red-500 focus:ring-offset-2'}`}
          >停止自动回复</button>
          <span className={`text-sm ${autoReplyOn ? 'text-green-600' : 'text-gray-600'}`}>当前状态：{autoReplyOn ? '已开启' : '未开启'}</span>
        </div>
        <div className="flex items-center gap-2">
          {opErr && <span className="text-sm text-red-600">{opErr}</span>}
          {opMsg && <span className="text-sm text-green-600">{opMsg}</span>}
        </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">待审核回复</h2>
            <span className="text-sm text-gray-500">每 3 秒刷新 · {drafts.length} 条</span>
          </div>
          {drafts.length === 0 ? (
            <div className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500">
              暂无待审核消息。目标联系人发来消息后，候选回复会出现在这里。
            </div>
          ) : (
            <div className="space-y-4">
              {drafts.map((draft) => (
                <div key={draft.id} className="rounded-lg border border-gray-200 p-4 space-y-3">
                  <div className="text-sm text-gray-700">
                    <span className="font-semibold">{draft.chat_name}</span>：{draft.incoming_message}
                  </div>
                  {draft.stickers?.some(Boolean) && (
                    <label className="flex items-center gap-2 text-xs text-gray-600">
                      <input
                        type="checkbox"
                        checked={Boolean(textOnly[draft.id])}
                        onChange={(e) => setTextOnly((prev) => ({ ...prev, [draft.id]: e.target.checked }))}
                      />
                      只发文字，不带表情包
                    </label>
                  )}
                  <div className="space-y-2">
                    {draft.options.map((option, index) => (
                      <button
                        key={`${draft.id}-${index}`}
                        type="button"
                        disabled={draftBusy !== null}
                        onClick={() => approveDraft(draft.id, index)}
                        className="block w-full rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-left text-sm text-primary-900 hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {draftBusy === `${draft.id}-${index}` ? "发送中…" : `${index + 1}. ${option}`}
                        {draft.stickers?.[index] && (
                          <span className={`mt-2 flex items-center gap-2 text-xs text-gray-500 ${textOnly[draft.id] ? "opacity-40" : ""}`}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={`${backendBase}/sticker-files/${String(draft.stickers[index]).split("/").map(encodeURIComponent).join("/")}`}
                              alt="表情包"
                              className="h-12 w-12 rounded object-contain bg-white"
                            />
                            + 表情包
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    disabled={draftBusy !== null}
                    onClick={() => rejectDraft(draft.id)}
                    className="rounded-md border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-60"
                  >
                    {draftBusy === `reject-${draft.id}` ? "处理中…" : "忽略"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
