# -*- coding: utf-8 -*-
"""微信监听（免费版 wxauto4 轮询方案）。

原理：wxauto4 免费版没有 AddListenChat / GetNextNewMessage（Plus 版专属），
因此采用轮询：GetSession() 找出目标会话的 isnew 标记 -> ChatWith 打开 -> GetAllMessage
读取新消息。打开聊天窗口会使微信标记为已读，因此同一条消息通常只被读到一次；
再用全局 seen 集合做兜底去重。

功能：
  1. 新消息追加写入 JSONL 日志（默认 data/listen.jsonl）
  2. 关键词自动回复（默认 data/reply_rules.json，keyword -> 回复文本，支持占位符）
  3. --once 单次轮询后退出（供 agent 快速检查）；否则持续轮询直到 Ctrl+C / --timeout
"""
import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime

import wx_common
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
    wx_init,
    UI_Lock,
)

MSG_EMOJI = {
    "text": "📝", "image": "🖼️", "file": "📎", "voice": "🎙️", "video": "🎬",
    "emotion": "😀", "quote": "💬", "time": "⏱️", "system": "⚙️", "other": "🔹",
}


def load_reply_rules(path):
    """读取关键词自动回复规则：{keyword: reply}，reply 支持 {who}{sender}{content} 占位。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[wx] 读取回复规则失败：{e}", file=sys.stderr)
    return {}


def msg_key(m, chat):
    """生成消息去重键（P0 修复）。

    原实现 (chat, sender, type, content) 缺少消息自身的唯一维度：同一人连发
    两条相同内容的消息会被 seen 漏掉第二条。修复：追加消息稳定标识
    msg.hash（切换 UI 不变，跨重启去重可靠）与 msg.id（窗口内唯一，同内容
    不同消息 id 不同，保证连发都受理）的组合。两者都拿不到时退化为原键。
    """
    try:
        content = getattr(m, "content", "") or ""
    except Exception:
        content = ""
    sender = getattr(m, "sender", "") or ""
    mtype = getattr(m, "type", "") or ""
    stable = []
    for attr in ("hash", "id"):
        try:
            v = getattr(m, attr, None)
            if v:
                stable.append(f"{attr}:{v}")
        except Exception:
            continue
    return (chat, sender, mtype, content, "|".join(stable))


def new_seen(maxlen=500):
    """新建去重集合（供监听/桥使用）。"""
    return deque(maxlen=maxlen)


def poll_once(wx, targets, seen, log_file, reply_rules, replied, chat_type_cache,
              group_whitelist=None, group_mention_only=False, my_aliases=None):
    """单次轮询。返回本次捕获的新消息列表。

    整个轮询（切窗/读消息/自动回复）持有跨进程 UI 锁，与桥/发送互斥，
    避免并发抢占微信窗口导致「发送假成功」或抢鼠标。

    群聊策略（P0）：记录照常写入日志；自动回复需过「白名单 + @ 我」
    （group_mention_only 开启时）。未通过时标记 reply_skipped 而不回复。
    """
    with UI_Lock():
        captured = []
        sessions = wx.GetSession() or []
        session_by_name = {}
        for s in sessions:
            info = getattr(s, "info", None) or {}
            name = info.get("name")
            if name:
                session_by_name[name] = info

        for target in targets:
            info = session_by_name.get(target)
            # 会话不在列表里或没有新消息则跳过（避免无谓打开窗口）
            if info is None or not info.get("isnew"):
                continue
            try:
                wx.ChatWith(who=target, exact=True)
            except Exception as e:
                print(f"[wx] 切换到 {target} 失败：{e}", file=sys.stderr)
                continue
            try:
                msgs = wx.GetAllMessage() or []
            except Exception as e:
                print(f"[wx] 读取 {target} 消息失败：{e}", file=sys.stderr)
                continue
            if not msgs:
                continue
            ctype = chat_type_cache.get(target)
            if ctype is None:
                try:
                    ctype = (wx.ChatInfo() or {}).get("chat_type", "")
                except Exception:
                    ctype = ""
                chat_type_cache[target] = ctype

            for m in msgs:
                key = msg_key(m, target)
                if key in seen:
                    continue
                seen.append(key)
                try:
                    content = getattr(m, "content", "") or ""
                except Exception:
                    content = ""
                try:
                    sender = getattr(m, "sender", "") or ""
                except Exception:
                    sender = ""
                mtype = getattr(m, "type", "") or ""
                attr = getattr(m, "attr", "") or ""

                record = {
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "chat": target,
                    "chat_type": ctype,
                    "attr": attr,
                    "type": mtype,
                    "sender": sender,
                    "content": content,
                }
                # 尽力提取 @ 名单（群聊 @ 检测用；失败忽略）
                at_names = capture_at(m)
                if at_names:
                    record["at"] = at_names
                captured.append(record)

                # 写入 JSONL
                try:
                    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                except Exception as e:
                    print(f"[wx] 写日志失败：{e}", file=sys.stderr)

                # 关键词自动回复（仅好友/群友发来的消息；群聊需过白名单 + @ 策略）
                if attr == "friend" and reply_rules:
                    allow_reply = True
                    if is_group_chat(record):
                        in_wl = (not group_whitelist) or (target in group_whitelist)
                        mentioned = (not group_mention_only) or mentioned_me(content, my_aliases, record.get("at"))
                        allow_reply = in_wl and mentioned
                        if not allow_reply:
                            record["reply_skipped"] = "group_policy"  # 记录照常，仅不回复
                    if allow_reply:
                        reply = None
                        for kw, rtext in reply_rules.items():
                            if kw and kw in content:
                                reply = rtext
                                break
                        if reply is not None:
                            rkey = (target, key)
                            if rkey not in replied:
                                try:
                                    text = reply.replace("{who}", target).replace("{sender}", sender).replace("{content}", content)
                                    wx.SendMsg(msg=text, who=target, clear=True, exact=True)
                                    record["reply_sent"] = True
                                    record["reply"] = text
                                    replied.add(rkey)
                                except Exception as e:
                                    record["reply_error"] = str(e)

                # 打印一条（人类可读）
                emoji = MSG_EMOJI.get(mtype, "🔹")
                print(f"{record['ts']} {emoji}[{target}][{sender}] {content}"
                      + ("  -> 已自动回复" if record.get("reply_sent") else ""), flush=True)
        return captured


def main():
    parser = argparse.ArgumentParser(description="微信消息监听（轮询）")
    add_common_args(parser)
    parser.add_argument("--who", default="", help="监听聊天（逗号分隔，默认用配置 listen_chats）")
    parser.add_argument("--interval", type=float, help="轮询间隔秒数（默认取配置，3）")
    parser.add_argument("--log", help="JSONL 日志路径（默认 data/listen.jsonl）")
    parser.add_argument("--reply-rules", help="关键词自动回复规则文件（默认 data/reply_rules.json）")
    parser.add_argument("--once", action="store_true", help="只轮询一次即退出")
    parser.add_argument("--timeout", type=int, default=0, help="持续轮询的最长秒数（0=不限，供后台 job 限时）")
    parser.add_argument("--force", action="store_true", help="忽略设置里的监听开关（listenEnabled）强行启动")
    args = parser.parse_args()

    cfg = load_config()
    # 监听开关守卫：设置里 listenEnabled=false 时拒绝启动（除非 --force）
    if not cfg.get("listen_enabled") and not args.force:
        print("错误：监听开关未开启：请在 DSH 设置（wxauto → listenEnabled）打开，或用 --force 临时启动。", file=sys.stderr)
        return 1
    targets = [t.strip() for t in (args.who or ",".join(cfg.get("listen_chats") or [])).split(",") if t.strip()]
    # 群聊白名单的群自动加入监听（白名单 = 允许响应的群，同时也被监听）
    for g in (cfg.get("group_whitelist") or []):
        g = str(g).strip()
        if g and g not in targets:
            targets.append(g)
    if not targets:
        out_text("错误：未指定监听聊天（--who 或配置 listen_chats，或群聊白名单 group_whitelist）。")
        return 1
    # 互斥守卫：桥已在轮询这些聊天时，监听不重复轮询（双进程同时操作微信窗口 = 抢鼠标翻倍）
    if not args.force and cfg.get("bridge_enabled"):
        bridge_chats = set(cfg.get("bridge_chats") or [])
        overlap = [t for t in targets if t in bridge_chats]
        if overlap:
            print(f"错误：聊天 {overlap} 已由双向桥轮询（bridgeChats 包含它们）。为避免双进程抢鼠标，"
                  f"请关闭 listenEnabled 或把 listenChats 改为其他聊天；或用 --force 强行双开。", file=sys.stderr)
            return 1
    interval = args.interval if args.interval is not None else float(cfg.get("listen_interval") or 3)
    log_file = resolve_path(cfg, "listen_log", "data/listen.jsonl") if not args.log else (
        args.log if os.path.isabs(args.log) else os.path.join(os.getcwd(), args.log))
    reply_file = args.reply_rules if args.reply_rules else resolve_path(cfg, "reply_rules_file", "data/reply_rules.json")
    reply_rules = load_reply_rules(reply_file)

    seen = deque(maxlen=500)
    replied = set()
    chat_type_cache = {}

    # 单实例守卫：与桥共用同一把锁，同一时间只允许一个 wxauto 守护进程，
    # 防止「桥 + 监听」双开同时轮询同一聊天导致重复受理/重复推送。
    singleton = SingleInstance(resolve_path(cfg, "single_lock", "data/wx_daemon.lock"))
    if not singleton.acquire():
        print("⚠️ 检测到已有另一个 wxauto 守护进程在运行（单实例锁被占用），"
              "本进程退出，避免重复轮询/重复回复。若确需重启，请先关闭旧进程。", file=sys.stderr)
        return 0  # 正常退出码：手动 wx-listen.ps1 看护不会因此反复重启

    wx = wx_init(debug=args.debug)
    # 群聊策略（P0）：白名单 + @ 响应（仅作用于自动回复；记录不受限）
    group_whitelist = [str(x).strip() for x in (cfg.get("group_whitelist") or []) if str(x).strip()]
    group_mention_only = bool(cfg.get("group_mention_only", True))
    my_aliases = resolve_my_aliases(wx, cfg.get("my_aliases"))
    if group_mention_only and not my_aliases:
        print("⚠️ 群聊 @ 策略已开启但无法确定你的昵称（GetMyInfo 为空且未配置 my_aliases）："
              "群聊消息将不会自动回复。可在 DSH 设置 wxauto → myAliases 配置昵称别名。", file=sys.stderr)
    print("⚠️ 监听运行期间会打开微信聊天窗口读取消息，可能干扰手动操作微信；"
          "完成后在 DSH 设置关闭 wxauto → listenEnabled 即停止。", flush=True)
    print(f"[wx] 监听中：{', '.join(targets)}（轮询间隔 {interval}s，日志 {log_file}）"
          + (f"，自动回复规则 {len(reply_rules)} 条" if reply_rules else "")
          + (f"，群聊策略：白名单={group_whitelist or '不限'}，{'仅 @ 我' if group_mention_only else '全部'}"
             if group_whitelist or group_mention_only else ""), flush=True)

    if args.once:
        captured = poll_once(wx, targets, seen, log_file, reply_rules, replied, chat_type_cache,
                             group_whitelist, group_mention_only, my_aliases)
        if args.json:
            out_json({"ok": True, "captured": captured})
        return 0

    # 持续轮询
    deadline = time.time() + args.timeout if args.timeout > 0 else None
    try:
        while True:
            poll_once(wx, targets, seen, log_file, reply_rules, replied, chat_type_cache,
                      group_whitelist, group_mention_only, my_aliases)
            if deadline and time.time() >= deadline:
                print(f"[wx] 已达 --timeout {args.timeout}s，退出。", flush=True)
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[wx] 监听已停止。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
