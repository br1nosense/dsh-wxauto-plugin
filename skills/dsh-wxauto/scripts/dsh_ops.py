# -*- coding: utf-8 -*-
"""DSH 运维 CLI：list / new / switch / active / status / shot / history / task / cancel。

供 agent 直接调用（agent 侧），也可被 wx_bridge 复用其核心函数。
所有命令基于 DSH 的 RPC API（见 dsh_api.py）。

状态：
  - "active" 会话记录在 data/bridge_state.json（--state 可覆盖）。
  - new 创建后自动设为 active；switch 按列表序号或 sessionId 前缀切换。
"""
import argparse
import json
import os
import sys
from datetime import datetime

import wx_common
from dsh_api import DshClient, DshError, extract_messages, projections_of, session_title
from dsh_card import render_card
from dsh_html_card import render_progress_card
from wx_common import add_common_args, load_config, out_json, out_text, resolve_path


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_active(client, state):
    sid = state.get("active")
    if not sid:
        return None
    if client.get_session(sid) is None:
        return None
    return sid


def build_session_info(client, sid, max_msgs=6):
    """组装 render_card 需要的 info dict。"""
    session = client.get_session(sid)
    h = client.history(sid, max_messages=40)
    proj = projections_of(h)
    msgs = extract_messages(h.get("events") or [])[-max_msgs:]
    return {
        "session_id": sid,
        "title": session_title(session, h),
        "running": bool(session and session.get("running")),
        "provider": (proj.get("request") or {}).get("provider") if isinstance(proj.get("request"), dict) else None,
        "model": (proj.get("request") or {}).get("model") if isinstance(proj.get("request"), dict) else None,
        "stats": proj.get("sessionStats") or {},
        "todos": proj.get("todos") or [],
        "plan": proj.get("plan") or {},
        "context": proj.get("contextPressure") or {},
        "messages": msgs,
    }


def fmt_sessions(items, active=None):
    lines = []
    for i, s in enumerate(items):
        sid = s.get("sessionId", "")
        running = "●" if s.get("running") else "○"
        blank = "空" if s.get("blank") else ""
        proj = (s.get("projections") or {}).get("values") or {}
        title = proj.get("title") or ""
        mark = "▶" if sid == active else " "
        updated = datetime.fromtimestamp((s.get("updatedAt") or 0) / 1000).strftime("%m-%d %H:%M")
        lines.append(f"  {mark} [{i}] {running} {title or sid[:22]}  {updated} {blank}")
    return lines


def cmd_list(client, args, state):
    items = client.list_sessions()
    active = state.get("active")
    if args.json:
        out_json({"ok": True, "active": active, "items": items})
    else:
        out_text(["DSH 会话列表：", *fmt_sessions(items, active)])
    return 0


def cmd_new(client, args, state):
    sid = client.create_session(cwd=args.cwd or None)
    state["active"] = sid
    save_state(args.state, state)
    if args.json:
        out_json({"ok": True, "sessionId": sid, "active": sid})
    else:
        out_text(f"✅ 新建会话：{sid}（已设为当前）")
    return 0


def cmd_switch(client, args, state):
    items = client.list_sessions()
    if not items:
        out_text("错误：当前没有会话。")
        return 1
    target = None
    if args.target is None:
        out_text("用法：switch <序号|sessionId前缀>")
        return 1
    if args.target.isdigit() and int(args.target) < len(items):
        target = items[int(args.target)]
    else:
        for s in items:
            if s.get("sessionId", "").startswith(args.target):
                target = s
                break
    if target is None:
        out_text(f"未找到匹配：{args.target}")
        return 1
    state["active"] = target["sessionId"]
    save_state(args.state, state)
    if args.json:
        out_json({"ok": True, "sessionId": target["sessionId"]})
    else:
        out_text(f"✅ 已切换：{target['sessionId']}")
    return 0


def cmd_active(client, args, state):
    sid = get_active(client, state)
    if args.json:
        out_json({"ok": bool(sid), "sessionId": sid})
    else:
        if sid:
            info = build_session_info(client, sid, max_msgs=0)
            out_text(f"当前会话：{sid}（{info.get('title') or '未命名'}）")
        else:
            out_text("当前没有 active 会话。用 /new 或 /switch 指定。")
    return 0


def cmd_status(client, args, state):
    sid = args.session or get_active(client, state)
    if not sid:
        out_text("未指定会话（--session 或先 /new//switch 设置 active）。")
        return 1
    info = build_session_info(client, sid)
    if args.json:
        out_json({"ok": True, "info": info})
    else:
        lines = [
            f"会话：{sid}",
            f"标题：{info.get('title') or '（未命名）'}",
            f"状态：{'● 运行中' if info.get('running') else '○ 空闲'}",
        ]
        st = info.get("stats") or {}
        lines.append(f"统计：{st.get('turns','—')} 轮 / {st.get('steps','—')} 步 / LLM {st.get('llmMs','—')}ms / 输出 {st.get('decodeTokens','—')} tok")
        todos = info.get("todos") or []
        if todos:
            done = sum(1 for t in todos if str(t.get('status')) == 'completed')
            lines.append(f"任务清单：{done}/{len(todos)} 完成")
            for t in todos:
                mark = "✔" if str(t.get("status")) == "completed" else "○"
                lines.append(f"  {mark} {t.get('content','')}")
        out_text(lines)
    return 0


def cmd_shot(client, args, state):
    sid = args.session or get_active(client, state)
    if not sid:
        out_text("未指定会话。")
        return 1
    info = build_session_info(client, sid)
    shots_dir = resolve_path(load_config(), "shots_dir", "data/shots")
    fname = f"dsh_{sid.split('-')[-1]}_{datetime.now().strftime('%H%M%S')}.png"
    path = os.path.join(shots_dir, fname)
    render_progress_card(info, path)
    if args.json:
        out_json({"ok": True, "path": path, "sessionId": sid, "renderer": "browser"})
    else:
        out_text(f"✅ 已生成进度截图：{path}")
    return 0


def cmd_history(client, args, state):
    sid = args.session or get_active(client, state)
    if not sid:
        out_text("未指定会话。")
        return 1
    h = client.history(sid, max_messages=args.count)
    msgs = extract_messages(h.get("events") or [])
    if args.json:
        out_json({"ok": True, "sessionId": sid, "messages": msgs})
    else:
        lines = [f"[{sid}] 最近 {len(msgs)} 条：", ""]
        for m in msgs:
            who = "👤" if m["role"] == "user" else "🤖"
            lines.append(f"  {who} {m['text']}")
            lines.append("")
        out_text(lines)
    return 0


def cmd_task(client, args, state):
    sid = args.session or get_active(client, state)
    if not sid:
        out_text("未指定会话（--session 或先设置 active）。")
        return 1
    task_text = args.target if args.target is not None else ""
    if not task_text.strip():
        out_text("用法：task <要做的事>")
        return 1
    # 记录起始 seq（用 turn/end 检测完成）
    before = client.history(sid, max_messages=1)
    from_seq = max((e.get("event", {}).get("seq", 0) for e in before.get("events", [])), default=0)
    client.prompt(sid, task_text.strip())
    if not args.wait:
        out_text(f"✅ 已受理并开始执行（会话 {sid}）。稍后用 status/history 查看进度。")
        return 0
    try:
        end_seq, reason = client.wait_turn_end(sid, timeout=args.timeout, from_seq=from_seq)
    except DshError as e:
        out_text(f"等待完成：{e}")
        return 1
    h = client.history(sid, max_messages=20)
    msgs = extract_messages(h.get("events") or [])
    new_assistant = [m for m in msgs if m["role"] == "assistant" and m["seq"] > from_seq]
    kind = (reason or {}).get("kind") if isinstance(reason, dict) else None
    if args.json:
        out_json({"ok": kind == "completed", "sessionId": sid, "reason": reason, "messages": msgs})
    else:
        if kind == "completed":
            out_text(["✅ 任务完成：", ""])
        elif kind in ("aborted", "cancelled"):
            out_text([f"⚠️ 任务被取消（reason={kind}）：", ""])
        elif kind in ("error", "blocked", "max-tokens", "interrupted"):
            out_text([f"❌ 任务未完成（{kind}）：", ""])
        else:
            out_text(["⚠️ 任务已结束但未确认完成：", ""])
        if new_assistant:
            for m in new_assistant:
                out_text(f"🤖 {m['text']}")
                out_text("")
        else:
            out_text("（未检测到新的助手回复）")
    return 0


def cmd_cancel(client, args, state):
    sid = args.session or get_active(client, state)
    if not sid:
        out_text("未指定会话。")
        return 1
    client.cancel(sid)
    out_text(f"✅ 已请求取消（会话 {sid}）。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="DSH 运维 CLI")
    add_common_args(parser)
    parser.add_argument("command", help="list|new|switch|active|status|shot|history|task|cancel")
    parser.add_argument("target", nargs="?", default=None, help="switch 的序号/前缀，或 task 的任务文本")
    parser.add_argument("--session", help="显式指定 sessionId（缺省用 active）")
    parser.add_argument("--cwd", help="new 时的工作目录")
    parser.add_argument("--count", type=int, default=20, help="history 条数")
    parser.add_argument("--wait", action="store_true", help="task 后等待完成")
    parser.add_argument("--timeout", type=int, default=600, help="等待完成超时（秒）")
    parser.add_argument("--base", help="DSH web 地址（默认 http://127.0.0.1:3080）")
    parser.add_argument("--state", help="状态文件路径（默认 data/bridge_state.json）")
    args = parser.parse_args()

    cfg = load_config()
    base = args.base or cfg.get("dsh_base") or "http://127.0.0.1:3080"
    state_path = args.state or resolve_path(cfg, "bridge_state", "data/bridge_state.json")
    args.state = state_path  # handler 统一用已解析的绝对路径
    state = load_state(state_path)

    client = DshClient(base=base)
    handlers = {
        "list": cmd_list, "new": cmd_new, "switch": cmd_switch, "active": cmd_active,
        "status": cmd_status, "shot": cmd_shot, "history": cmd_history,
        "task": cmd_task, "cancel": cmd_cancel,
    }
    if args.command not in handlers:
        out_text(f"未知命令：{args.command}。可用：{', '.join(handlers)}")
        return 1
    try:
        return handlers[args.command](client, args, state)
    except DshError as e:
        out_text(f"错误：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
