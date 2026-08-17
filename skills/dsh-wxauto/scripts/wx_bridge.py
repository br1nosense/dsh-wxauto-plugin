# -*- coding: utf-8 -*-
"""微信 ⇄ DSH 一体化守护进程：桥 + 监听整合为单个进程。

监听「控制聊天」（config bridge_chats / listenChats）里的消息：
  - / 开头的消息 → 命令（help/new/list/switch/active/status/shot/history/cancel/task）
  - 命中关键词自动回复规则（data/reply_rules.json）→ 自动回复（不当作任务）
  - 其他消息 → 按模式处理：
      full 模式（默认）：当作任务文本发给 active DSH 会话执行（session.prompt），
        完成后自动推送助手的最终回复（分片发送）+ 浏览器渲染的进度截图；
      listen 模式：仅记录到 JSONL（不做任务），即原 wx_listen 的行为。

进度展示：/shot 与任务完成推送会把 DSH 会话状态渲染成 HTML，用 headless 浏览器
截成 PNG（dsh_html_card.py）通过微信发送 —— 即「web UI 层面」的进度截图。

依赖：
  - wxauto4（发送/监听微信）
  - DSH web 服务在 http://127.0.0.1:3080（RPC API，回环免认证）

运行（DSH 中以后台任务启动）：
  wx-bridge.ps1 -Who "我的控制群" -Timeout 86400            # full 模式
  wx-bridge.ps1 -Who "我的控制群" -Timeout 86400 -Listen    # 纯监听模式
"""
import argparse
import atexit
import faulthandler
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

# 可选依赖：websocket-client（用于把 DSH 的 ask_user_question 提问转发到微信）。
# 未安装时提问转发降级为不可用，桥其余功能不受影响。
try:
    from websocket import create_connection as _ws_create
    from websocket import WebSocketTimeoutException as _WsTimeout
    _HAS_WS = True
except Exception:
    _HAS_WS = False

# 捕获原生层崩溃（COM/UIA 致命错误常导致静默退出）的 traceback
faulthandler.enable()
atexit.register(lambda: print("[bridge] 进程退出", file=sys.stderr, flush=True))

import wx_common
import wx_listen
from dsh_api import DshClient, DshError, extract_messages, projections_of, session_title
from dsh_card import render_card
from dsh_html_card import render_progress_card
from wx_common import (
    SingleInstance,
    add_common_args,
    capture_at,
    is_group_chat,
    load_config,
    mentioned_me,
    out_json,
    resolve_my_aliases,
    resolve_path,
    strip_mention_prefix,
    wx_init,
    UI_Lock,
)

HELP_TEXT = (
    "【DSH 微信遥控】可用命令：\n"
    "/new - 新建对话\n"
    "/list - 会话列表\n"
    "/switch <序号|前缀> - 切换对话\n"
    "/active - 当前对话\n"
    "/status - 查看进度\n"
    "/shot - 发送进度截图\n"
    "/history [n] - 最近对话\n"
    "/task <内容> - 执行任务\n"
    "/cancel - 取消当前会话回合\n"
    "/stop - 终止当前会话任务（取消+清理待推送）\n"
    "/stopall - 终止本聊天所有卡死任务（含已归档会话）\n"
    "/help - 帮助\n"
    "直接发普通消息 = 把内容作为任务交给 DSH 执行\n"
    "群聊中需 @ 我（且在白名单内）才会响应；可在设置调整 groupMentionOnly / groupWhitelist"
)


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active_by_chat": {}, "active": None}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


class Bridge:
    def __init__(self, cfg, state_path):
        self.cfg = cfg
        self.state_path = state_path
        self.state = load_state(state_path)
        self.base = cfg.get("dsh_base") or "http://127.0.0.1:3080"
        self.cwd = cfg.get("dsh_cwd")
        self.shots_dir = resolve_path(cfg, "shots_dir", "data/shots")
        self.timeout = int(cfg.get("dsh_task_timeout") or 900)
        self.pending_file = resolve_path(cfg, "bridge_pending", "data/bridge_pending.json")
        self.reply_rules_file = resolve_path(cfg, "reply_rules_file", "data/reply_rules.json")
        self.wx = None
        self.lock = threading.RLock()  # 串行化所有微信 UI 操作（wxauto4 非线程安全）
        self._last_text = {}  # chat -> (text, ts)：短窗口防重复发送（P0 修复）
        # ask_user_question 转发状态（mux 监听线程 + 等待线程共享）
        self._waits = {}        # session_id -> {chat, task}（桥正在等待完成的会话）
        self._pending_q = {}    # session_id -> {rpc_id, questions, chat, asked_at}
        self._last_question_rpc = {}  # session_id -> rpc_id：提问转发去重（P0）
        self._mux_stop = threading.Event()
        self._mux_url = None
        self._question_timeout = int(cfg.get("question_timeout") or 600)
        # 群聊策略（P0）：白名单 + @ 响应 + 发送者鉴权。群聊消息需在群白名单内、
        # （开启时）@ 我才受理，且发送者必须在 sender_whitelist 内。
        # 回答桥发起的提问不受限。群白名单为空 = 不限制（所有被监听的群按 @ 策略处理）。
        # sender_whitelist（P0 安全）：空 = 未配置 → 群聊默认拒绝所有成员驱动 agent；
        # ["*"] = 显式开放（不做 sender 鉴权）；具体昵称列表 = 只允许名单内的人。
        self.group_whitelist = [str(x).strip() for x in (cfg.get("group_whitelist") or []) if str(x).strip()]
        self.group_mention_only = bool(cfg.get("group_mention_only", True))
        self.my_aliases = [str(x).strip() for x in (cfg.get("my_aliases") or []) if str(x).strip()]
        self.sender_whitelist = [str(x).strip() for x in (cfg.get("sender_whitelist") or []) if str(x).strip()]

    def sender_allowed(self, sender):
        """群聊发送者鉴权：sender 是否被允许驱动 agent。

        - sender_whitelist 含 "*" → 显式开放，任何人（除自己/系统）都允许。
        - sender_whitelist 配置了具体昵称 → 只允许名单内的人。
        - sender_whitelist 为空（未配置）→ 默认拒绝（安全方向）。
        """
        sender = (sender or "").strip()
        if not sender or sender == "self":
            return False
        if "*" in self.sender_whitelist:
            return True
        if not self.sender_whitelist:
            return False
        return sender in self.sender_whitelist

    def bind_wx(self, wx):
        self.wx = wx

    def load_reply_rules(self):
        """加载关键词自动回复规则（整合 wx_listen 的功能）。"""
        try:
            return wx_listen.load_reply_rules(self.reply_rules_file)
        except Exception as e:
            print(f"[bridge] 加载回复规则失败：{e}", file=sys.stderr)
            return {}

    # ---- 待推送持久化（崩溃重启后补发任务完成推送） ----
    def _load_pending(self):
        try:
            with open(self.pending_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return []

    def _save_pending(self, chat, sid, task, from_seq):
        items = [p for p in self._load_pending() if not (p.get("chat") == chat and p.get("sid") == sid)]
        items.append({"chat": chat, "sid": sid, "task": task, "from_seq": from_seq, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        try:
            os.makedirs(os.path.dirname(self.pending_file) or ".", exist_ok=True)
            with open(self.pending_file, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[bridge] 写待推送失败：{e}", file=sys.stderr)

    def _clear_pending(self, chat, sid):
        items = [p for p in self._load_pending() if not (p.get("chat") == chat and p.get("sid") == sid)]
        try:
            with open(self.pending_file, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[bridge] 清除待推送失败：{e}", file=sys.stderr)

    # ---- 终止卡死任务（手动归档/中断后的清理）----
    def _clear_all_pending_for_chat(self, chat):
        """清空某聊天的所有待推送（含卡死任务），返回清掉的条数。"""
        items = self._load_pending()
        removed = [p for p in items if p.get("chat") == chat]
        if removed:
            try:
                with open(self.pending_file, "w", encoding="utf-8") as f:
                    json.dump([p for p in items if p.get("chat") != chat], f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[bridge] 清空待推送失败：{e}", file=sys.stderr)
        return len(removed)

    def _abort_session(self, chat, sid):
        """终止指定会话正在执行的任务：cancel 回合 + 清 pending + 清等待/提问状态。

        用于手动归档对话或中途中断后，解除桥卡死在该会话上的状态。
        返回 True 表示已处理；sid 为空时返回 False。
        """
        if not sid:
            return False
        # 1) 清等待/提问状态（先解除 ask_user_question 挂起，避免 agent 继续等）
        self._unregister_wait(sid)
        # 2) 清该会话的待推送
        self._clear_pending(chat, sid)
        # 3) cancel 会话当前回合（若还存在）
        try:
            dsh = DshClient(base=self.base)
            dsh.cancel(sid)
        except DshError:
            pass  # 会话已归档/不存在，无需 cancel
        except Exception as e:
            print(f"[bridge] 终止会话异常 {sid[:8]}: {e}", file=sys.stderr)
        return True

    def _abort_all_for_chat(self, chat):
        """终止某聊天所有卡死任务：cancel 全部关联会话 + 清空 pending/wait/提问。"""
        sids = set()
        pending_total = 0
        for p in self._load_pending():
            if p.get("chat") == chat and p.get("sid"):
                sids.add(p["sid"])
                pending_total += 1
        with self.lock:
            for sid in list(self._waits.keys()):
                if self._waits[sid].get("chat") == chat:
                    sids.add(sid)
            for sid in list(self._pending_q.keys()):
                if self._pending_q[sid].get("chat") == chat:
                    sids.add(sid)
        for sid in sids:
            self._abort_session(chat, sid)
        # 兜底清空（_abort_session 已逐条清，这里确保无残留）
        self._clear_all_pending_for_chat(chat)
        return len(sids), pending_total

    def _cleanup_archived(self, chat, sid):
        """归档/中断自动清理：会话已不存在（被手动归档）时，解除其 pending/wait 状态。

        返回 True 表示该会话已失效并清理；False 表示会话仍有效。
        """
        if not sid:
            return False
        try:
            dsh = DshClient(base=self.base)
            alive = dsh.get_session(sid) is not None
        except Exception:
            return False
        if alive:
            return False
        # 会话已被归档/删除：清掉桥侧残留状态，避免重启后继续卡死
        print(f"[bridge] 会话 {sid[:18]} 已不存在（可能被归档），自动清理其待推送/等待状态。", flush=True)
        self._unregister_wait(sid)
        self._clear_pending(chat, sid)
        return True

    # ---- state ----
    def _save(self):
        save_state(self.state_path, self.state)

    def active_for(self, chat):
        sid = self.state.get("active_by_chat", {}).get(chat)
        if sid:
            dsh = DshClient(base=self.base)
            if dsh.get_session(sid) is not None:
                return sid
        return self.state.get("active")

    def set_active(self, chat, sid):
        self.state.setdefault("active_by_chat", {})[chat] = sid
        self.state["active"] = sid
        self._save()

    # ---- send helpers（共享同一微信实例 + 线程锁 + 跨进程 UI 锁，失败重试） ----
    def _ensure_window(self, chat):
        """确保当前窗口是该聊天；已在则跳过（省去搜索+点击，减少抢鼠标）。"""
        try:
            cur = (self.wx.ChatInfo() or {}).get("chat_name")
            if cur == chat:
                return True
        except Exception:
            pass
        try:
            self.wx.ChatWith(who=chat, exact=True)
            return True
        except Exception as e:
            print(f"[bridge] 切换到 {chat} 失败：{e}", file=sys.stderr)
            return False

    def send_text(self, chat, text, retries=1):
        """发送文本到微信聊天，防重复发送。

        防重复（P0 修复）：wxauto4 的 SendMsg 偶发「实际已发送成功，但返回的
        status 不是『成功』」——旧实现此时会走重试分支**再发一条**，导致同一条
        回复在微信里出现多条（重复回复 bug）。因此：
          - 每次实际调用 SendMsg 前先做「短时间窗口同 chat 同文本」去重
            （1.5s 内完全相同的文本不重发），即使底层误判也能挡住重复。
          - SendMsg 调用一次即视为「已发送」并记录（无论响应如何），
            仅在 _ensure_window 失败等明确未发送的情况下才考虑重试。
        """
        text = (text or "").strip()
        if not text:
            return True
        with UI_Lock():
            with self.lock:
                now = time.time()
                last_sent = self._last_text.get(chat)
                if last_sent and last_sent[0] == text and (now - last_sent[1]) < 1.5:
                    # 同一聊天 1.5s 内已发过完全相同文本 → 视为已发送，跳过防重复
                    return True
                for i in range(retries + 1):
                    if not self._ensure_window(chat):
                        if i < retries:
                            time.sleep(0.8)
                            continue
                        return False
                    try:
                        resp = self.wx.SendMsg(msg=text, who=chat, clear=True, exact=True)
                        # 已调用 SendMsg：无论响应如何都视为已发送（防重试导致重复）
                        self._last_text[chat] = (text, time.time())
                        return True
                    except Exception as e:
                        print(f"[bridge] 发送文本失败 [{chat}]: {e}", file=sys.stderr)
                        if i < retries:
                            time.sleep(0.8)
                            continue
            return False

    def send_text_multi(self, chat, text, max_len=450):
        """分片发送长文本：每片不超过 max_len 字符（wxauto4 超长文本会超时丢失）。

        按行优先聚合，尽量保持语义完整；逐片发送并小睡，返回是否全部成功。
        """
        text = (text or "").strip()
        if not text:
            return True
        if len(text) <= max_len:
            return self.send_text(chat, text)
        # 按行聚合为 ≤max_len 的片
        chunks = []
        buf = ""
        for line in text.split("\n"):
            if not line:
                buf += "\n"
                continue
            while len(line) > max_len:  # 超长单行硬切
                chunk = line[:max_len]
                line = line[max_len:]
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(chunk)
            candidate = (buf + line + "\n") if buf else (line + "\n")
            if len(candidate) > max_len:
                if buf:
                    chunks.append(buf)
                buf = line + "\n"
            else:
                buf = candidate
        if buf:
            chunks.append(buf)
        ok = True
        for chunk in chunks:
            if not self.send_text(chat, chunk.strip()):
                ok = False
            time.sleep(0.6)
        return ok

    def send_file(self, chat, path, timeout=30):
        """发送文件/截图（进度截图等）。

        长驻 wxauto 实例的 SendFiles 偶发「卡死数十秒 / 返回成功却未送达」，
        且卡死时持有进程内锁，无法用超时安全打断（会冻住整个桥）。
        因此统一改走独立短命子进程 wx_send.py：新实例 + 跨进程 UI 锁 +
        safe_send 校验返回状态 + 可整体超时强杀，最稳。
        返回是否发送成功。
        """
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        skill_dir = os.path.dirname(scripts_dir)
        wx_send = os.path.join(scripts_dir, "wx_send.py")
        cmd = [sys.executable, wx_send, "--who", chat, "--file", path, "--json"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=skill_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = proc.returncode == 0
            tail = (proc.stdout or "").strip().splitlines()
            tail = tail[-1] if tail else ""
            print(f"[bridge] → 子进程发送文件 [{chat}] ok={ok} rc={proc.returncode} {os.path.basename(path)}", flush=True)
            if not ok:
                err = (proc.stderr or "").strip() or tail or "(无输出)"
                print(f"[bridge] 子进程发送文件失败 [{chat}] {path}:\n{err}", file=sys.stderr)
            return ok
        except subprocess.TimeoutExpired:
            print(f"[bridge] 子进程发送文件超时（>{timeout}s），已终止 [{chat}] {path}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[bridge] 子进程发送文件异常 [{chat}] {path}: {type(e).__name__}: {e}", file=sys.stderr)
            return False

    # ---- ask_user_question 转发：mux 监听 + 微信回答 ----
    def _register_wait(self, session_id, chat, task):
        with self.lock:
            self._waits[session_id] = {"chat": chat, "task": task}

    def _unregister_wait(self, session_id):
        with self.lock:
            self._waits.pop(session_id, None)
            q = self._pending_q.pop(session_id, None)
        if q:
            self._cancel_pending(session_id, q)

    def _has_pending_question(self, session_id):
        with self.lock:
            return session_id in self._pending_q

    def _pending_question_for_chat(self, chat):
        with self.lock:
            for sid, q in list(self._pending_q.items()):
                if q.get("chat") == chat:
                    return sid, dict(q)
        return None, None

    def _cancel_pending(self, session_id, q):
        try:
            dsh = DshClient(base=self.base)
            dsh.cancel_question(q["rpc_id"], session_id)
        except Exception as e:
            print(f"[bridge] 取消提问失败 {session_id}: {e}", file=sys.stderr)

    def _format_questions(self, questions):
        lines = ["📝 DSH 需要你选择/回答："]
        for i, q in enumerate(questions, 1):
            lines.append(f"\n【问题{i}】{q.get('question') or ''}")
            if q.get("detail"):
                lines.append(f"（{q.get('detail')}）")
            opts = q.get("options") or []
            for j, o in enumerate(opts, 1):
                desc = o.get("description")
                lines.append(f"  {j}. {o.get('label')}" + (f" —— {desc}" if desc else ""))
            if q.get("multiSelect"):
                lines.append("（可多选，用逗号分隔）")
        lines.append("\n请直接回复：选项编号或选项内容；多问题用「序号: 答案」分行；回复 0 表示跳过。")
        return "\n".join(lines)

    def _handle_question(self, session_id, rpc_id, questions):
        # 防重复推送（P0）：同一 rpc_id 的提问只转发一次，避免 mux 重复事件
        # 导致用户收到多条相同问题、回复也重复。
        if rpc_id and rpc_id == self._last_question_rpc.get(session_id):
            return
        with self.lock:
            wait = self._waits.get(session_id)
            if not wait:
                return  # 不是桥正在等待的会话，忽略
            chat = wait["chat"]
            old = self._pending_q.pop(session_id, None)
        if old:
            self._cancel_pending(session_id, old)
        with self.lock:
            self._pending_q[session_id] = {
                "rpc_id": rpc_id, "questions": questions,
                "chat": chat, "asked_at": time.time(),
            }
            if rpc_id:
                self._last_question_rpc[session_id] = rpc_id
        print(f"[bridge] 检测到提问 session={session_id[:8]} chat={chat}，已转发微信", flush=True)
        self.send_text(chat, self._format_questions(questions))

    def _sweep_questions(self):
        now = time.time()
        stale = []
        with self.lock:
            for sid, q in list(self._pending_q.items()):
                if now - q.get("asked_at", 0) > self._question_timeout:
                    stale.append((sid, q))
            for sid, q in stale:
                self._pending_q.pop(sid, None)
        for sid, q in stale:
            print(f"[bridge] 提问超时（>{self._question_timeout}s）自动取消 {sid[:8]}", flush=True)
            self._cancel_pending(sid, q)
            try:
                self.send_text(q.get("chat"), "⏰ 问题等待超时，已自动跳过，DSH 继续执行。")
            except Exception:
                pass

    def _mux_loop(self):
        if not self._mux_url:
            return
        print(f"[bridge] mux 监听启动：{self._mux_url}", flush=True)
        while not self._mux_stop.is_set():
            ws = None
            try:
                ws = _ws_create(self._mux_url, timeout=15)
                ws.settimeout(5)
                while not self._mux_stop.is_set():
                    try:
                        raw = ws.recv()
                    except _WsTimeout:
                        self._sweep_questions()
                        continue
                    except Exception:
                        break  # 连接断开 → 重连
                    try:
                        full = json.loads(raw)
                    except Exception:
                        continue
                    payload = full.get("payload") or {}
                    if payload.get("type") != "question/requested":
                        continue
                    self._handle_question(payload.get("sessionId"),
                                          full.get("rpcId"), payload.get("questions") or [])
            except Exception as e:
                print(f"[bridge] mux 异常：{type(e).__name__}: {e}", file=sys.stderr)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            self._sweep_questions()
            if self._mux_stop.is_set():
                break
            time.sleep(3)
        print("[bridge] mux 监听停止", flush=True)

    def _parse_answer(self, content, question):
        qid = question.get("id")
        opts = question.get("options") or []
        multi = bool(question.get("multiSelect"))
        text = (content or "").strip()
        if text.lower() in ("0", "跳过", "skip", "none", "-", "无", "不选"):
            return {"id": qid, "selected": []}

        def norm(s):
            return re.sub(r"[\s。.，,：:、()（）\"'“”‘’]+", "", (s or "").lower())

        def match(part):
            part = (part or "").strip().strip(" .:：()（）\t")
            if not part:
                return None
            if part.isdigit():
                i = int(part)
                if 1 <= i <= len(opts):
                    return opts[i - 1].get("label")
            pn = norm(part)
            for o in opts:
                lab = o.get("label") or ""
                if lab and norm(lab) == pn:
                    return lab
                for sep in (":", "：", ".", "、", ")"):
                    if sep in lab:
                        head, _, tail = lab.partition(sep)
                        if (tail and norm(tail) == pn) or (head and norm(head) == pn):
                            return lab
            return None

        if multi:
            parts = [p for p in re.split(r"[,，;；、]+", text) if p.strip()]
            selected = []
            for p in parts:
                m = match(p)
                if m and m not in selected:
                    selected.append(m)
            return {"id": qid, "selected": selected} if selected else {"id": qid, "custom": text}
        m = match(text)
        if m:
            return {"id": qid, "selected": [m]}
        return {"id": qid, "custom": text}

    def _build_answers(self, content, questions):
        if len(questions) == 1:
            return [self._parse_answer(content, questions[0])]
        answers = [{"id": q.get("id"), "selected": []} for q in questions]
        lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
        if not lines:
            lines = [p.strip() for p in re.split(r"[;；]", content or "") if p.strip()]
        for ln in lines:
            m = re.match(r"^\s*(\d+)\s*[:：.．]\s*(.*)$", ln)
            if m:
                idx = int(m.group(1))
                if 1 <= idx <= len(questions):
                    answers[idx - 1] = self._parse_answer(m.group(2), questions[idx - 1])
            else:
                for i, q in enumerate(questions):
                    a = answers[i]
                    if not a.get("selected") and not a.get("custom"):
                        answers[i] = self._parse_answer(ln, q)
                        break
        return answers

    def try_answer_question(self, chat, content, require_sender=True, sender=""):
        """把控制聊天的普通消息当作待回答问题的答案提交。消费掉返回 True。

        require_sender=True 且 sender 未通过 sender_allowed 鉴权时，不提交答案
        （防群聊里任何人替 agent 做选择），仅返回 True 以消费该消息避免误当任务。
        """
        sid, q = self._pending_question_for_chat(chat)
        if not q:
            return False
        if require_sender and not self.sender_allowed(sender):
            print(f"[bridge] 群聊回答提问被拒：发送者 {sender or '未知'} 不在 sender_whitelist", flush=True)
            return True
        try:
            answers = self._build_answers(content, q["questions"])
            dsh = DshClient(base=self.base)
            resp = dsh.answer_question(q["rpc_id"], sid, {"answers": answers})
            accepted = bool(resp.get("accepted"))
        except Exception as e:
            print(f"[bridge] 提交答案异常 {sid[:8]}: {e}", file=sys.stderr)
            try:
                self.send_text(chat, f"提交答案出错：{e}，请重新回复。")
            except Exception:
                pass
            return True
        if accepted:
            with self.lock:
                self._pending_q.pop(sid, None)
            try:
                self.send_text(chat, "✅ 已收到你的回答，DSH 继续执行中…")
            except Exception:
                pass
            print(f"[bridge] 已提交微信回答 chat={chat} accepted=True", flush=True)
        else:
            try:
                self.send_text(chat, f"答案未通过校验（{resp.get('reason')}），请重新回复上面的问题。")
            except Exception:
                pass
        return True

    # ---- 会话信息 ----
    def _info(self, sid, max_msgs=6):
        dsh = DshClient(base=self.base)
        session = dsh.get_session(sid)
        h = dsh.history(sid, max_messages=40)
        proj = projections_of(h)
        msgs = extract_messages(h.get("events") or [])[-max_msgs:]
        return {
            "session_id": sid,
            "title": session_title(session, h),
            "running": bool(session and session.get("running")),
            "stats": proj.get("sessionStats") or {},
            "todos": proj.get("todos") or [],
            "plan": proj.get("plan") or {},
            "context": proj.get("contextPressure") or {},
            "messages": msgs,
        }

    def _status_text(self, sid):
        dsh = DshClient(base=self.base)
        session = dsh.get_session(sid)
        h = dsh.history(sid, max_messages=10)
        proj = projections_of(h)
        lines = [
            f"标题：{session_title(session, h) or '（未命名）'}",
            f"状态：{'● 运行中' if (session or {}).get('running') else '○ 空闲'}",
        ]
        st = proj.get("sessionStats") or {}
        lines.append(f"统计：{st.get('turns','—')} 轮 / {st.get('steps','—')} 步 / LLM {st.get('llmMs','—')}ms / 输出 {st.get('decodeTokens','—')} tok")
        todos = proj.get("todos") or []
        if todos:
            done = sum(1 for t in todos if str(t.get('status')) == 'completed')
            lines.append(f"任务清单：{done}/{len(todos)} 完成")
            for t in todos[-6:]:
                mark = "✔" if str(t.get("status")) == "completed" else "○"
                lines.append(f"  {mark} {t.get('content','')}")
        return "\n".join(lines)

    def _shot_path(self, sid):
        info = self._info(sid)
        fname = f"dsh_{sid.split('-')[-1]}_{datetime.now().strftime('%H%M%S')}.png"
        path = os.path.join(self.shots_dir, fname)
        render_progress_card(info, path)  # 浏览器渲染（web UI 风格），无浏览器时回退 PIL
        return path

    # ---- 命令处理：返回要发送的 (kind, payload) 列表 ----
    def handle(self, chat, content):
        """处理一条控制消息，返回 [(kind, payload)]，kind in {text, file}。任何异常都不外泄。"""
        content = (content or "").strip()
        if not content:
            return []
        try:
            if content.startswith("/"):
                return self._handle_command(chat, content)
            return self._handle_task(chat, content)
        except Exception as e:
            print(f"[bridge] 处理消息异常 [{chat}]: {type(e).__name__}: {e}", file=sys.stderr)
            return [("text", f"处理消息出错：{e}")]

    def _handle_command(self, chat, content):
        parts = content.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            dsh = DshClient(base=self.base)
            sid = self.active_for(chat)
            if cmd == "/help":
                return [("text", HELP_TEXT)]
            if cmd == "/new":
                # 新建对话：先清理旧 active 会话的卡死残留（归档/中断后），再新建
                old_sid = self.active_for(chat)
                if old_sid:
                    self._abort_session(chat, old_sid)
                nsid = dsh.create_session(cwd=self.cwd or None)
                self.set_active(chat, nsid)
                return [("text", f"✅ 已新建对话：{nsid}\n已设为当前。")]
            if cmd == "/list":
                items = dsh.list_sessions()
                lines = [f"DSH 会话（{len(items)} 个，▶=当前）："]
                for i, s in enumerate(items):
                    s2 = s.get("sessionId", "")
                    title = ((s.get("projections") or {}).get("values") or {}).get("title") or ""
                    mark = "▶" if s2 == sid else " "
                    lines.append(f"{mark}[{i}] {'●' if s.get('running') else '○'} {title or s2[:22]}")
                return [("text", "\n".join(lines[:20]))]
            if cmd == "/switch":
                if not rest:
                    return [("text", "用法：/switch <序号|sessionId前缀>")]
                items = dsh.list_sessions()
                target = None
                if rest.isdigit() and int(rest) < len(items):
                    target = items[int(rest)]
                else:
                    for s in items:
                        if s.get("sessionId", "").startswith(rest):
                            target = s
                            break
                if not target:
                    return [("text", f"未找到：{rest}")]
                self.set_active(chat, target["sessionId"])
                return [("text", f"✅ 已切换：{target['sessionId']}")]
            if cmd == "/active":
                if sid:
                    return [("text", f"当前：{sid}\n{self._status_text(sid).splitlines()[0]}")]
                return [("text", "当前没有 active 会话，请 /new 或 /switch")]
            if cmd == "/status":
                if not sid:
                    return [("text", "当前没有 active 会话，请 /new 或 /switch")]
                return [("text", self._status_text(sid))]
            if cmd == "/shot":
                if not sid:
                    return [("text", "当前没有 active 会话，请 /new 或 /switch")]
                try:
                    path = self._shot_path(sid)
                except Exception as e:
                    return [("text", f"生成截图失败：{e}")]
                return [("text", "📷 DSH 进度截图："), ("file", path)]
            if cmd == "/history":
                if not sid:
                    return [("text", "当前没有 active 会话")]
                n = int(rest) if rest.isdigit() else 10
                h = dsh.history(sid, max_messages=n)
                msgs = extract_messages(h.get("events") or [])
                lines = [f"最近 {len(msgs)} 条："]
                for m in msgs[-10:]:
                    who = "👤" if m["role"] == "user" else "🤖"
                    lines.append(f"{who} {m['text']}")
                return [("text", "\n".join(lines))]
            if cmd in ("/cancel", "/stop"):
                if not sid:
                    return [("text", "当前没有 active 会话")]
                self._abort_session(chat, sid)
                verb = "已取消回合并清理待推送" if cmd == "/cancel" else "已终止任务（取消回合+清理待推送）"
                return [("text", f"✅ {verb}（{sid}）")]
            if cmd == "/stopall":
                n_sids, n_pending = self._abort_all_for_chat(chat)
                return [("text", f"✅ 已终止本聊天所有卡死任务：{n_sids} 个会话、{n_pending} 条待推送已清理。")]
            if cmd == "/task":
                return self._handle_task(chat, rest)
            return [("text", f"未知命令：{cmd}\n/help 查看帮助")]
        except DshError as e:
            return [("text", f"错误：{e}")]
        except Exception as e:
            print(f"[bridge] 命令处理异常 [{chat}] {cmd}: {type(e).__name__}: {e}", file=sys.stderr)
            return [("text", f"命令执行出错：{e}")]

    def _handle_task(self, chat, text):
        try:
            dsh = DshClient(base=self.base)
            sid = self.active_for(chat)
            # 归档/中断自动清理：active 会话已被手动归档（get_session 不存在）时，
            # 解除其残留 pending/wait，避免桥继续卡死在该会话上。
            if sid and self._cleanup_archived(chat, sid):
                sid = None
            # 关键：active 会话正在运行（忙）时不能排队 —— agent 若卡住（如等待用户
            # 回答的 ask_user_question），排进去的任务会一直不执行（「已受理但 DSH 无
            # 执行」）。忙时自动新建会话执行，保证任务真正跑起来。
            busy = False
            if sid:
                try:
                    busy = dsh.is_running(sid)
                except Exception:
                    busy = False
            if not sid or busy:
                # 无 active，或 active 正忙 → 新建会话执行
                try:
                    sid = dsh.create_session(cwd=self.cwd or None)
                    self.set_active(chat, sid)
                    created = True
                except DshError as e:
                    return [("text", f"新建会话失败：{e}")]
            else:
                created = False
            try:
                before = dsh.history(sid, max_messages=1)
                from_seq = max((e.get("event", {}).get("seq", 0) for e in before.get("events", [])), default=0)
                dsh.prompt(sid, text)
            except DshError as e:
                return [("text", f"提交任务失败：{e}")]
            head = f"✅ 已新建对话 {sid}\n" if created else ""
            # 先持久化待推送（桥崩溃重启后自动补发），再起后台线程等待完成
            self._save_pending(chat, sid, text, from_seq)
            t = threading.Thread(target=self._wait_and_push, args=(chat, sid, text, from_seq), daemon=True)
            t.start()
            return [("text", f"{head}已受理任务，正在执行：{text}\n完成后会推送结果与进度截图。")]
        except Exception as e:
            print(f"[bridge] 任务处理异常 [{chat}]: {type(e).__name__}: {e}", file=sys.stderr)
            return [("text", f"任务处理出错：{e}")]

    def _wait_and_push(self, chat, sid, task, from_seq):
        dsh = DshClient(base=self.base)
        self._register_wait(sid, chat, task)
        try:
            # 归档/中断自动清理：会话已被手动归档/删除时，不再等待其回合结束
            # （否则 wait_turn_end 会一直等到超时，桥卡死在该任务上），如实上报并清理。
            if self._cleanup_archived(chat, sid):
                self._clear_pending(chat, sid)
                try:
                    self.send_text(chat, f"⚠️ 会话 {sid[:18]} 已被归档/删除，任务「{task}」已终止。")
                except Exception:
                    pass
                return
            # 关键：必须把 from_seq 传给 wait_turn_end —— 否则它会匹配到会话里更早的
            # 旧回合 turn/end，任务还没跑完就提前返回，导致「任务没完成却推送了完成」。
            # keep_waiting：等用户回答 ask_user_question 提问时顺延超时，不误判结束。
            _, reason = dsh.wait_turn_end(
                sid, timeout=self.timeout, from_seq=from_seq,
                keep_waiting=lambda: self._has_pending_question(sid))
            h = dsh.history(sid, max_messages=60)
            msgs = extract_messages(h.get("events") or [])
            new_asst = [m for m in msgs if m["role"] == "assistant" and m["seq"] > from_seq]
            reply = "\n".join(m["text"] for m in new_asst).strip() or "（无文本回复）"
            kind = (reason or {}).get("kind") if isinstance(reason, dict) else None
            # 只有确认完成才报「完成」；失败/取消/未知如实上报，绝不假装成功。
            if kind == "completed" or (kind is None and reply != "（无文本回复）"):
                # 明确 completed，或兜底返回但确有新助手回复 → 完成
                head = f"✅ 任务完成：{task}"
                body = reply
            elif kind in ("aborted", "cancelled"):
                head = f"⚠️ 任务被取消：{task}"
                body = reply
            elif kind in ("error", "blocked", "max-tokens", "interrupted"):
                head = f"❌ 任务未完成（{kind}）：{task}"
                body = reply
            else:
                # 兜底且无新回复 / 原因未知：如实说明，不假装完成
                head = f"⚠️ 任务已结束但未确认完成：{task}"
                body = reply
            # 短消息直接发；长回复分片发送（wxauto4 超长文本会超时丢失内容）
            if len(head) + len(body) <= 450:
                self.send_text(chat, f"{head}\n\n{body}")
            else:
                self.send_text(chat, head)
                time.sleep(0.8)
                self.send_text_multi(chat, body)
            try:
                path = self._shot_path(sid)
                self.send_file(chat, path)
            except Exception as e:
                print(f"[bridge] 推送进度截图失败：{e}", file=sys.stderr)
            self._clear_pending(chat, sid)  # 推送完成才清除待推送
        except DshError as e:
            self.send_text(chat, f"任务处理异常：{e}")
        except Exception as e:
            print(f"[bridge] 任务完成推送异常 [{chat}]: {type(e).__name__}: {e}", file=sys.stderr)
            try:
                self.send_text(chat, f"任务执行出错：{e}")
            except Exception:
                pass
        finally:
            self._unregister_wait(sid)


def load_seen(path):
    """加载上次已处理的消息键（跨重启去重）。JSON 存的是数组，需还原为可哈希元组。

    P0 修复：key 现在是 5 元组（含 hash|id 稳定标识），不再截断为前 4 项。
    兼容旧格式：4 元组（旧键）补空稳定位，避免升级后把旧消息重复受理。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            keys = set()
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) >= 4:
                    t = tuple(item)
                    if len(t) < 5:  # 旧 4 元组 → 补第 5 位（空稳定标识）
                        t = t + ("",)
                    keys.add(t)
            return keys
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        print(f"[bridge] 读取已处理消息失败：{e}", file=sys.stderr)
    return set()


def save_seen(path, keys, cap=3000):
    lst = list(keys)[-cap:]
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False)
    except Exception as e:
        print(f"[bridge] 保存已处理消息失败：{e}", file=sys.stderr)


def prime_seen(wx, chats, seen):
    """启动时把控制聊天当前可见的消息全部标记为已处理：只处理启动之后的新消息。"""
    for chat in chats:
        try:
            wx.ChatWith(who=chat, exact=True)
            for m in (wx.GetAllMessage() or []):
                seen.add(wx_listen.msg_key(m, chat))
        except Exception as e:
            print(f"[bridge] 预读 {chat} 失败：{e}", file=sys.stderr)


def poll_control_chats(wx, chats, seen, seen_file, log_file, chat_type_cache, last_read, force_interval):
    """控制聊天轮询（省鼠标 + 兜底强读）。

    策略（大幅减少"抢鼠标"）：
      - GetSession() 查各控制聊天的 isnew（不切换窗口）；
      - 有新消息（isnew）：若当前窗口不是该聊天才 ChatWith 切换，然后读取；
      - 无新消息但当前窗口正好是该聊天：直接 GetAllMessage 读取（不切换）；
      - 无 isnew 标记（聊天被折叠/不在 GetSession 顶层列表）：
          控制聊天是用户明确指定的，**定期兜底强制切过去读一次**（force_interval，
          默认 30s），否则这类聊天收到消息永远不反应。
          实测：被微信折叠进「折叠的聊天」的会话，GetSession 里看不到（拿不到
          isnew），但 ChatWith + GetAllMessage 仍能读到新消息。

    去重：入锁前合并磁盘 seen（跨进程兜底，双进程同时轮询时也不会重复受理），
    捕获新消息后立即持久化，让其他进程下一轮能看到。
    """
    # 跨进程 seen 合并：本函数在 UI_Lock 内执行，读-改-写与同锁的其他进程串行，
    # 即使出现双进程也不会对同一条消息重复受理。
    disk = load_seen(seen_file)
    if disk:
        seen |= disk
    captured = []
    now = time.time()
    try:
        sessions = wx.GetSession() or []
        new_flag = {}
        for s in sessions:
            info = getattr(s, "info", None) or {}
            if info.get("name"):
                new_flag[info["name"]] = bool(info.get("isnew"))
    except Exception as e:
        print(f"[bridge] GetSession 失败：{e}", file=sys.stderr)
        new_flag = {}

    for chat in chats:
        is_new = new_flag.get(chat, False)
        last = last_read.get(chat, 0)
        # 有 isnew → 立即读；没有 isnew（被折叠/不在列表）→ 每 force_interval 兜底强读一次
        if not is_new and (now - last) < force_interval:
            # 窗口在别处且无新消息（且未到兜底强读间隔）：不切换，避免抢鼠标
            continue
        try:
            cur = (wx.ChatInfo() or {}).get("chat_name")
        except Exception:
            cur = None
        if cur != chat:
            try:
                wx.ChatWith(who=chat, exact=True)
            except Exception as e:
                print(f"[bridge] 切换到 {chat} 失败：{e}", file=sys.stderr)
                continue
        try:
            msgs = wx.GetAllMessage() or []
        except Exception as e:
            print(f"[bridge] 读取 {chat} 失败：{e}", file=sys.stderr)
            continue
        last_read[chat] = now
        ctype = chat_type_cache.get(chat)
        if ctype is None:
            try:
                ctype = (wx.ChatInfo() or {}).get("chat_type", "")
            except Exception:
                ctype = ""
            chat_type_cache[chat] = ctype
        for m in msgs:
            key = wx_listen.msg_key(m, chat)
            if key in seen:
                continue
            seen.add(key)
            try:
                content = getattr(m, "content", "") or ""
            except Exception:
                content = ""
            try:
                sender = getattr(m, "sender", "") or ""
            except Exception:
                sender = ""
            rec = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "chat": chat,
                "chat_type": ctype,
                "attr": getattr(m, "attr", "") or "",
                "type": getattr(m, "type", "") or "",
                "sender": sender,
                "content": content,
            }
            # 尽力提取 @ 名单（群聊 @ 检测用；失败忽略）
            try:
                atn = capture_at(m)
                if atn:
                    rec["at"] = atn
            except Exception:
                pass
            captured.append(rec)
            try:
                os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"[bridge] 写日志失败：{e}", file=sys.stderr)
    # 捕获了新消息就立即持久化（跨进程去重的关键，其他进程下一轮合并后即跳过）
    if captured:
        save_seen(seen_file, seen)
    return captured


def main():
    parser = argparse.ArgumentParser(description="微信⇄DSH 一体化守护进程（桥 + 监听整合）")
    add_common_args(parser)
    parser.add_argument("--who", default="", help="控制聊天（逗号分隔，默认 full 用 bridge_chats，listen 用 listen_chats）")
    parser.add_argument("--interval", type=float, help="轮询间隔（默认取配置）")
    parser.add_argument("--timeout", type=int, default=0, help="最长运行秒数（0=不限）")
    parser.add_argument("--once", action="store_true", help="只处理一次即退出")
    parser.add_argument("--allow-self", action="store_true", help="也处理自己发的消息（便于用文件传输助手自测）")
    parser.add_argument("--force", action="store_true", help="忽略设置开关（bridgeEnabled/listenEnabled）强行启动")
    parser.add_argument("--mode", choices=["full", "listen"], default="", help="full=命令/任务+自动回复+推送；listen=仅记录+关键词自动回复")
    parser.add_argument("--listen", action="store_true", help="等价于 --mode listen")
    parser.add_argument("--log", help="JSONL 日志路径（默认 data/bridge.jsonl）")
    parser.add_argument("--reply-rules", help="关键词自动回复规则文件（默认 data/reply_rules.json）")
    parser.add_argument("--probe", action="store_true", help="只输出开关/配置状态后退出（供 supervisor 预检）")
    parser.add_argument("--state", help="状态文件路径")
    args = parser.parse_args()

    cfg = load_config()
    if args.probe:
        out_json({
            "bridge_enabled": bool(cfg.get("bridge_enabled")),
            "listen_enabled": bool(cfg.get("listen_enabled")),
            "bridge_chats": cfg.get("bridge_chats") or [],
            "listen_chats": cfg.get("listen_chats") or [],
            "group_whitelist": cfg.get("group_whitelist") or [],
            "group_mention_only": bool(cfg.get("group_mention_only", True)),
            "my_aliases": cfg.get("my_aliases") or [],
            "sender_whitelist": cfg.get("sender_whitelist") or [],
        })
        return 0

    # 模式：--mode 优先；否则桥开关开=full，仅监听开关开=listen，都开=full（桥已覆盖监听）
    mode = args.mode or ("listen" if args.listen else "")
    if not mode:
        mode = "full" if cfg.get("bridge_enabled") else ("listen" if cfg.get("listen_enabled") else "full")

    # 单一总开关守卫（兼容旧字段）；除非 --force
    if not (cfg.get("enabled") or cfg.get("bridge_enabled") or cfg.get("listen_enabled")) and not args.force:
        out_json({"ok": False, "error": "总开关未开启：请在 DSH 设置页「微信自动化」打开「总开关」（wxauto → enabled），或用 --force 临时启动"})
        return 1

    if mode == "listen":
        default_chats = cfg.get("listen_chats") or []
    else:
        default_chats = cfg.get("bridge_chats") or (cfg.get("listen_chats") or [])
    chats = [c.strip() for c in (args.who or ",".join(default_chats)).split(",") if c.strip()]
    # 群聊白名单的群自动加入轮询（白名单 = 允许响应的群，同时也被监听）。
    # 否则用户只填 groupWhitelist 时群消息根本不会被轮询到（实测踩坑点）。
    for g in (cfg.get("group_whitelist") or []):
        g = str(g).strip()
        if g and g not in chats:
            chats.append(g)
    if not chats:
        out_json({"ok": False, "error": f"未指定聊天（--who 或配置 {'listen_chats' if mode=='listen' else 'bridge_chats'}，或群聊白名单 group_whitelist）"})
        return 1
    interval = args.interval if args.interval is not None else float(cfg.get("bridge_poll_interval") or cfg.get("listen_interval") or 8)
    state_path = args.state or resolve_path(cfg, "bridge_state", "data/bridge_state.json")
    bridge = Bridge(cfg, state_path)
    if args.reply_rules:
        bridge.reply_rules_file = args.reply_rules if os.path.isabs(args.reply_rules) else os.path.join(os.getcwd(), args.reply_rules)

    log_file = resolve_path(cfg, "bridge_log", "data/bridge.jsonl") if not args.log else (
        args.log if os.path.isabs(args.log) else os.path.join(os.getcwd(), args.log))
    print("⚠️ 守护进程运行期间将占用本机微信窗口（自动化操作），期间请勿手动操作微信；"
          "完成后在 DSH 设置关闭对应开关即停止。", flush=True)
    print(f"[bridge] 一体化守护启动：模式={mode}，聊天 {chats}，DSH {bridge.base}", flush=True)

    seen_file = resolve_path(cfg, "bridge_seen", "data/bridge_seen.json")
    seen_file_existed = os.path.exists(seen_file)
    seen = load_seen(seen_file)
    chat_type_cache = {}
    last_read = {}
    # 兜底强读间隔：控制聊天在 GetSession 里看不到 isnew 时（被折叠等），
    # 每隔这么多秒强制切过去读一次，保证收到消息有反应。默认 30s。
    force_interval = float(cfg.get("bridge_force_interval") or cfg.get("listen_force_interval") or 30)

    # 单实例守卫：同一时间只允许一个 wxauto 守护进程（桥/监听整合为同一进程）。
    # 防止「插件自动拉起 + 手动 wx-bridge.ps1 看护」等多开 → 双进程同时轮询同一
    # 聊天 → 同一条消息被重复受理/重复推送（任务重复受理的根因之一）。
    singleton = SingleInstance(resolve_path(cfg, "single_lock", "data/wx_daemon.lock"))
    if not singleton.acquire():
        print("[bridge] ⚠️ 检测到已有另一个 wxauto 守护进程在运行（单实例锁被占用），"
              "本进程退出，避免重复受理消息。若确需重启，请先关闭旧进程。", file=sys.stderr)
        out_json({"ok": False, "error": "已有另一个 wxauto 守护进程在运行（单实例锁被占用）。为避免重复受理，请先关闭旧进程。"})
        return 0  # 正常退出码：手动 wx-bridge.ps1 看护不会因此反复重启

    wx = wx_init(debug=args.debug)
    bridge.bind_wx(wx)
    # 群聊策略：解析「我」的昵称别名（@ 检测用），并打印策略状态
    bridge.my_aliases = resolve_my_aliases(wx, bridge.my_aliases)
    if bridge.group_mention_only and not bridge.my_aliases:
        print("[bridge] ⚠️ 群聊 @ 策略已开启但无法确定你的昵称（GetMyInfo 为空且未配置 my_aliases）："
              "群聊消息将不会响应。可在 DSH 设置 wxauto → myAliases 配置昵称别名。", file=sys.stderr)
    _sender_txt = (",".join(bridge.sender_whitelist)
                   if bridge.sender_whitelist
                   else "默认拒绝（未配置，群聊无人能驱动 agent）")
    print(f"[bridge] 群聊策略：白名单={bridge.group_whitelist or '不限'}，"
          f"{'仅响应 @ 我' if bridge.group_mention_only else '响应全部消息'}（我的昵称别名：{bridge.my_aliases or '未知'}）"
          f"，发送者白名单={_sender_txt}",
          flush=True)
    # 预读当前可见消息为已处理：仅首次运行（seen 文件不存在）才做，
    # 重启时 seen 已持久化，无需再切窗口（避免抢鼠标）
    if not seen_file_existed:
        with UI_Lock():
            with bridge.lock:
                prime_seen(wx, chats, seen)
        save_seen(seen_file, seen)
        print(f"[bridge] 首次运行：已预读 {len(seen)} 条历史消息为已处理。", flush=True)

    # 恢复待推送：桥之前崩溃时未完成的任务完成推送，重启后补发。
    # 归档/中断自动清理：恢复前检查会话是否还存在，已归档的直接清掉待推送，
    # 避免重启后又去等一个已归档会话的回合（卡死 + 反复补发）。
    pending = bridge._load_pending()
    revived = 0
    dropped = 0
    for p in pending:
        if not (p.get("chat") and p.get("sid") and p.get("task")):
            continue
        sid = p["sid"]
        try:
            alive = DshClient(base=bridge.base).get_session(sid) is not None
        except Exception:
            alive = True  # DSH 查询失败时保守补发，等 _wait_and_push 再兜底
        if not alive:
            print(f"[bridge] 丢弃待推送（会话 {sid[:18]} 已归档/删除）：{p['task'][:30]}", flush=True)
            bridge._clear_pending(p["chat"], sid)
            dropped += 1
            continue
        print(f"[bridge] 恢复待推送：{p['chat']} / {sid[:18]} / {p['task'][:30]}", flush=True)
        t = threading.Thread(target=bridge._wait_and_push,
                             args=(p["chat"], sid, p["task"], p.get("from_seq", 0)), daemon=True)
        t.start()
        revived += 1
    if dropped:
        print(f"[bridge] 恢复待推送完成：补发 {revived}，丢弃已归档 {dropped}", flush=True)

    # ask_user_question 转发：订阅 DSH events.mux，把提问推送到微信并接收回答。
    # 仅 full 模式；未安装 websocket-client 时降级（桥其余功能不受影响）。
    bridge._mux_url = f"{bridge.base.replace('https://', 'wss://').replace('http://', 'ws://')}/api/events.mux"
    bridge._question_timeout = int(cfg.get("question_timeout") or 600)
    if mode == "full" and _HAS_WS:
        threading.Thread(target=bridge._mux_loop, daemon=True).start()
    elif mode == "full":
        print("[bridge] ⚠️ 未安装 websocket-client，无法把 DSH 提问转发到微信（pip install websocket-client）", file=sys.stderr)

    reply_rules = bridge.load_reply_rules()
    reply_reload_at = time.time() + 60  # 每分钟重载规则，编辑即时生效

    def process(captured):
        nonlocal reply_rules, reply_reload_at
        now = time.time()
        if now >= reply_reload_at:
            reply_rules = bridge.load_reply_rules()
            reply_reload_at = now + 60
        for rec in captured:
            content = (rec.get("content") or "").strip()
            attr = rec.get("attr")
            if attr == "self":
                # 默认忽略自己发的（避免回复循环）。
                # --allow-self 时也只处理 / 开头的命令，防止把桥自己的回复再当任务。
                if not (args.allow_self and content.startswith("/")):
                    continue
            elif attr != "friend":
                continue  # system/time/other 等忽略，避免把时间戳当任务
            if rec.get("chat") not in chats:
                continue

            # 群聊策略（P0）：群白名单 + 发送者白名单 + @ 响应。回答桥发起的提问豁免；
            # 消息记录已由轮询写入日志，这里只决定「是否响应」。
            group = is_group_chat(rec)
            eligible = True
            if group:
                # 1) 群白名单：白名单非空时必须在此群内
                if bridge.group_whitelist and rec["chat"] not in bridge.group_whitelist:
                    eligible = False
                # 2) 发送者鉴权（P0 安全）：只允许 sender_whitelist 内的成员驱动 agent。
                #    未配置 sender_whitelist → 群聊默认拒绝（防止群里任何人 @ 你即可操控）；
                #    配置 ["*"] → 显式开放，所有 @ 都接受。
                elif not bridge.sender_allowed(rec.get("sender", "")):
                    eligible = False
                    print(f"[bridge] 群聊 {rec['chat']} 发送者 {rec.get('sender','')} 不在 sender_whitelist，忽略", flush=True)
                # 3) @ 检查（开启时）
                elif bridge.group_mention_only and not mentioned_me(content, bridge.my_aliases, rec.get("at")):
                    eligible = False
                else:
                    # 通过策略：去掉开头的 @我的昵称，命令/回答才能解析
                    content = strip_mention_prefix(content, bridge.my_aliases)
            print(f"[bridge] ← [{rec['chat']}] {content}", flush=True)

            # 1) / 命令（两种模式都支持；群聊未通过策略则忽略）
            if content.startswith("/"):
                if not eligible:
                    print(f"[bridge] 群聊 {rec['chat']} 未通过策略（未 @ 我或不在白名单），忽略命令", flush=True)
                    continue
                for kind, payload in bridge.handle(rec["chat"], content):
                    if kind == "text":
                        ok = bridge.send_text(rec["chat"], payload)
                        print(f"[bridge] → text ok={ok}", flush=True)
                    elif kind == "file":
                        ok = bridge.send_file(rec["chat"], payload)
                        print(f"[bridge] → file {payload} ok={ok}", flush=True)
                continue

            # 2) 待回答的 DSH 提问：普通消息当作答案提交（优先于关键词/任务）。
            #    群聊中回答提问同样需要发送者通过鉴权（否则任何群成员都能替 agent 做选择）；
            #    私聊不受 sender_whitelist 限制（聊天窗口唯一确定对方）。
            if bridge.try_answer_question(rec["chat"], content,
                                          require_sender=group, sender=rec.get("sender", "")):
                continue

            # 群聊未通过策略：不自动回复、不当任务（仅记录）
            if not eligible:
                print(f"[bridge] 群聊 {rec['chat']} 未通过策略，忽略消息（仅记录）", flush=True)
                continue

            # 3) 关键词自动回复（整合 wx_listen；命中则不当作任务）
            reply = None
            for kw, rtext in (reply_rules or {}).items():
                if kw and kw in content:
                    reply = rtext
                    break
            if reply is not None:
                text = str(reply).replace("{who}", rec["chat"]).replace("{sender}", rec.get("sender", "")).replace("{content}", content)
                ok = bridge.send_text(rec["chat"], text)
                print(f"[bridge] → 自动回复 ok={ok}", flush=True)
                continue

            # 4) 普通消息：full 模式当任务；listen 模式仅记录（bridge.jsonl 已写）
            if mode == "full":
                for kind, payload in bridge.handle(rec["chat"], content):
                    if kind == "text":
                        ok = bridge.send_text(rec["chat"], payload)
                        print(f"[bridge] → text ok={ok}", flush=True)
                    elif kind == "file":
                        ok = bridge.send_file(rec["chat"], payload)
                        print(f"[bridge] → file {payload} ok={ok}", flush=True)

    def poll_locked():
        with UI_Lock():
            with bridge.lock:
                return poll_control_chats(wx, chats, seen, seen_file, log_file, chat_type_cache, last_read, force_interval)

    if args.once:
        try:
            captured = poll_locked()
            process(captured)
        except Exception as e:
            print(f"[bridge] 单次处理异常：{type(e).__name__}: {e}", file=sys.stderr)
        finally:
            save_seen(seen_file, seen)
        return 0

    # 连续失败计数：超过阈值自动重建微信连接（COM/UIA 瞬时错误自愈）
    consecutive_failures = 0

    def rebuild_wx():
        nonlocal wx
        print("[bridge] 连续失败，重新初始化微信连接…", flush=True)
        try:
            # wx_init 内部已持有跨进程 UI 锁（与监听/发送串行初始化），这里只需线程锁
            with bridge.lock:
                new_wx = wx_init(debug=args.debug)
                bridge.bind_wx(new_wx)
                wx = new_wx
            return True
        except Exception as e:
            print(f"[bridge] 重新初始化微信失败：{e}", file=sys.stderr)
            return False

    deadline = time.time() + args.timeout if args.timeout > 0 else None
    # 周期性重建微信连接（每 30 分钟），清除 COM/UIA 累积状态；间隔足够长以免频繁操作窗口
    REBUILD_INTERVAL = 1800
    last_rebuild = time.time()
    try:
        while True:
            now = time.time()
            if now - last_rebuild >= REBUILD_INTERVAL:
                if rebuild_wx():
                    last_rebuild = now
                    consecutive_failures = 0
            try:
                prev_seen_len = len(seen)
                captured = poll_locked()
                consecutive_failures = 0
                process(captured)
                if len(seen) != prev_seen_len:
                    save_seen(seen_file, seen)
            except Exception as e:
                consecutive_failures += 1
                print(f"[bridge] 轮询/处理异常（第 {consecutive_failures} 次）：{type(e).__name__}: {e}", file=sys.stderr)
                if consecutive_failures >= 3:
                    if rebuild_wx():
                        consecutive_failures = 0
                        last_rebuild = now
            if deadline and now >= deadline:
                print(f"[bridge] 已达 --timeout {args.timeout}s，退出。", flush=True)
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[bridge] 已停止。", file=sys.stderr)
    finally:
        bridge._mux_stop.set()
        save_seen(seen_file, seen)
    return 0


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
