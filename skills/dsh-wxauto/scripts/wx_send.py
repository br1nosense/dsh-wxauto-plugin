# -*- coding: utf-8 -*-
"""向指定微信聊天窗口发送任务进度 / 完成信息（文本或文件）。

支持：
  - 单个或多个目标（--who 逗号分隔；--who @config 展开为配置里的 target_chats）
  - 长文本：--msg 直接传，或 --msg-file 传入含换行的报告文件
  - 文件/图片：--file 传绝对路径（可多次传，或逗号分隔）
"""
import argparse
import os
import sys

import wx_common
from wx_common import add_common_args, load_config, out_json, out_text, resolve_path, safe_send, wx_init


def expand_who(who_arg, cfg):
    who_arg = who_arg.strip()
    if who_arg == "@config":
        return list(cfg.get("target_chats") or [])
    parts = [w.strip() for w in who_arg.split(",") if w.strip()]
    return parts or (list(cfg.get("target_chats") or []) if who_arg == "" else [])


def main():
    parser = argparse.ArgumentParser(description="向微信聊天窗口发送消息")
    add_common_args(parser)
    parser.add_argument("--who", default="", help="目标聊天（名称，可逗号分隔多个；@config 用配置的 target_chats）")
    parser.add_argument("--msg", default="", help="消息文本")
    parser.add_argument("--msg-file", help="消息文本文件路径（适合长/多行报告）")
    parser.add_argument("--file", action="append", default=[], help="要发送的文件/图片绝对路径（可多次传）")
    parser.add_argument("--no-exact", action="store_true", help="关闭精确匹配（默认精确匹配，避免发错窗口）")
    parser.add_argument("--at", default="", help="@对象（可选）")
    args = parser.parse_args()

    cfg = load_config()

    msg = args.msg
    if args.msg_file:
        p = args.msg_file if os.path.isabs(args.msg_file) else os.path.join(os.getcwd(), args.msg_file)
        with open(p, "r", encoding="utf-8") as f:
            msg = f.read()

    # @config 批量汇报：自动套用配置的前后缀，保持汇报格式统一
    bulk = args.who.strip() == "@config"
    if bulk and msg:
        prefix = cfg.get("report_prefix")
        tail = cfg.get("report_tail")
        if prefix:
            msg = str(prefix) + msg
        if tail:
            msg = msg + str(tail)

    files = []
    for f_ in args.file:
        files.extend(x.strip() for x in f_.split(",") if x.strip())

    if not msg and not files:
        out_text("错误：--msg / --msg-file / --file 至少要提供一个。")
        return 1
    if not (args.who or files):
        # 发文件时可以不指定 who？仍要求 who，保持简单
        pass

    targets = expand_who(args.who, cfg)
    if not targets:
        out_text("错误：未指定 --who，且配置 target_chats 为空。")
        return 1

    wx = wx_init(debug=args.debug)
    results = []
    for who in targets:
        ok, text = safe_send(wx, who, msg=msg, filepath=files, exact=not args.no_exact, at=args.at or None)
        results.append({"who": who, "ok": ok, "detail": text})

    success = all(r["ok"] for r in results)
    if args.json:
        out_json({"ok": success, "results": results, "sent_msg": msg, "sent_files": files})
    else:
        lines = []
        for r in results:
            lines.append(f"{'✅' if r['ok'] else '❌'} [{r['who']}] {r['detail']}")
        out_text(lines)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
