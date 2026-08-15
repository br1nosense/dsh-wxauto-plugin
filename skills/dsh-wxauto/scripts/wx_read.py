# -*- coding: utf-8 -*-
"""读取指定聊天窗口的最近消息，供 agent 查看对话上下文。"""
import argparse
import sys

import wx_common
from wx_common import add_common_args, out_json, out_text, wx_init


def msg_row(m):
    try:
        content = getattr(m, "content", "")
    except Exception:
        content = ""
    return {
        "attr": getattr(m, "attr", ""),
        "type": getattr(m, "type", ""),
        "sender": getattr(m, "sender", ""),
        "content": content,
    }


def main():
    parser = argparse.ArgumentParser(description="读取聊天窗口最近消息")
    add_common_args(parser)
    parser.add_argument("--who", required=True, help="目标聊天名称")
    parser.add_argument("--count", type=int, default=20, help="读取条数（默认 20）")
    args = parser.parse_args()

    wx = wx_init(debug=args.debug)
    wx.ChatWith(who=args.who, exact=True)
    msgs = wx.GetAllMessage() or []

    rows = [msg_row(m) for m in msgs[-args.count:]]
    if args.json:
        out_json({"ok": True, "who": args.who, "count": len(rows), "messages": rows})
    else:
        lines = [f"[{args.who}] 最近 {len(rows)} 条消息：", ""]
        for r in rows:
            who = r.get("sender") or (r.get("attr") or "")
            tag = {"text": "📝", "image": "🖼️", "file": "📎", "voice": "🎙️", "video": "🎬",
                   "emotion": "😀", "quote": "💬", "time": "⏱️", "system": "⚙️", "other": "🔹"}.get(r.get("type"), "🔹")
            lines.append(f"  {tag} [{who}] {r.get('content')}")
        out_text(lines)
    return 0


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
