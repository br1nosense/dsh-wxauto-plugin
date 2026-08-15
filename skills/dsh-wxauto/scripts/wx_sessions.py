# -*- coding: utf-8 -*-
"""列出当前微信会话，便于确认要向哪个聊天窗口发送/监听。"""
import argparse
import sys

import wx_common
from wx_common import add_common_args, out_json, out_text, wx_init


def session_row(s):
    info = getattr(s, "info", None) or {}
    return {
        "name": info.get("name"),
        "new_count": info.get("new_count"),
        "isnew": info.get("isnew"),
        "content": info.get("content"),
    }


def main():
    parser = argparse.ArgumentParser(description="列出微信会话列表")
    add_common_args(parser)
    parser.add_argument("--match", help="按名称模糊过滤（子串匹配）")
    parser.add_argument("--limit", type=int, default=50, help="最多返回条数")
    parser.add_argument("--only-new", action="store_true", help="只列出有新消息的会话")
    args = parser.parse_args()

    wx = wx_init(debug=args.debug)
    sessions = wx.GetSession() or []
    rows = []
    for s in sessions:
        row = session_row(s)
        if args.match and not (row.get("name") and args.match in row["name"]):
            continue
        if args.only_new and not row.get("isnew"):
            continue
        rows.append(row)
        if len(rows) >= args.limit:
            break

    if args.json:
        out_json({"ok": True, "count": len(rows), "sessions": rows})
    else:
        lines = [f"共 {len(rows)} 个会话：", ""]
        for r in rows:
            new = "🔔" if r.get("isnew") else "  "
            lines.append(f"  {new} {r.get('name')}  新消息:{r.get('new_count')}  最近:{r.get('content')}")
        out_text(lines)
    return 0


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
