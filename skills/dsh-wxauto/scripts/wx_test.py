# -*- coding: utf-8 -*-
"""环境预检：Python / wxauto4 / 微信客户端 / 登录状态。"""
import argparse
import json
import os
import sys

import wx_common
from wx_common import add_common_args, load_config, out_json, out_text, wx_init


def check_client_process():
    """检查微信主进程是否在运行。"""
    try:
        import psutil
    except Exception:
        return {"ok": False, "detail": "psutil 不可用"}
    names = ("Weixin.exe", "WeChat.exe", "WeixinApp.exe")
    procs = [p for p in psutil.process_iter(["name"]) if p.info.get("name") in names]
    if procs:
        return {"ok": True, "detail": f"运行中：{', '.join(sorted(set(p.info['name'] for p in procs)))}"}
    return {"ok": False, "detail": "未检测到微信主进程（Weixin.exe / WeChat.exe）"}


def main():
    parser = argparse.ArgumentParser(description="wxauto4 环境预检")
    add_common_args(parser)
    args = parser.parse_args()

    checks = {}

    # 1. Python
    checks["python"] = {"ok": True, "detail": sys.version.split()[0]}

    # 2. wxauto4 库
    try:
        import wxauto4
        checks["wxauto4"] = {"ok": True, "detail": getattr(wxauto4, "__version__", "?") or "已安装"}
    except ImportError as e:
        checks["wxauto4"] = {"ok": False, "detail": f"未安装 wxauto4（pip install wxauto4）：{e}"}

    # 3. 微信客户端进程
    checks["client"] = check_client_process()

    # 4. 连接与登录
    login = {"ok": False, "detail": "未执行"}
    myinfo = None
    if checks["wxauto4"]["ok"] and checks["client"]["ok"]:
        try:
            wx = wx_init(debug=args.debug)
            myinfo = wx.GetMyInfo()
            ok = bool(myinfo)
            login = {"ok": ok, "detail": json.dumps(myinfo, ensure_ascii=False) if myinfo else "GetMyInfo 返回空（可能未登录）"}
        except Exception as e:
            login = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    checks["login"] = login

    overall = all(v.get("ok") for v in checks.values())
    result = {"ok": overall, "checks": checks, "myinfo": myinfo}
    if args.json:
        out_json(result)
    else:
        lines = [f"环境预检：{'✅ 通过' if overall else '❌ 存在问题'}", ""]
        for name, c in checks.items():
            mark = "✅" if c.get("ok") else "❌"
            lines.append(f"  {mark} {name}: {c.get('detail')}")
        out_text(lines)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(wx_common.main_wrapper(main))
