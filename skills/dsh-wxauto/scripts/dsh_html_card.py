# -*- coding: utf-8 -*-
"""浏览器层面渲染的 DSH 进度截图：会话状态 → 深色 HTML → headless 浏览器截图 PNG。

用户诉求是"从浏览器层面下手"（web UI 截图）：用真实浏览器引擎渲染一张贴合
DSH web 暗色风格的进度页（标题/状态/统计/任务清单/计划/上下文/最近对话），
再用 headless Edge/Chrome 截成 PNG 经微信发送。静态 HTML 无长连接，headless
稳定退出；找不到浏览器时回退到 PIL 卡片（dsh_card.render_card）。
"""
import os
import subprocess
import sys
import tempfile
import time

import wx_common
from dsh_card import render_card as render_pil_card
from wx_common import load_config, resolve_path

WIDTH = 720

_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#14161d; color:#e2e6ee;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif; }
.wrap { padding: 20px 22px; }
.head { background:#1c212b; border:1px solid #2a3140; border-radius:14px;
  padding:14px 16px; margin-bottom:12px; }
.head .t { color:#5ab2ff; font-size:15px; font-weight:700; margin:0 0 6px; }
.head .title { font-size:15px; }
.head .meta { color:#8a94a6; font-size:12px; margin-top:6px; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; }
.run { background:#1e3a2b; color:#5ed08a; }
.idle { background:#262b36; color:#8a94a6; }
.card { background:#1b1f29; border:1px solid #2a3140; border-radius:12px;
  padding:12px 14px; margin-bottom:12px; }
.card .sec { color:#5ab2ff; font-size:13px; font-weight:700; margin:0 0 8px; }
.stats { font-size:13px; color:#c9d1df; }
.todo { font-size:13px; padding:3px 0; color:#c9d1df; }
.todo.done { color:#5ed08a; }
.msg { margin:8px 0; padding:9px 12px; border-radius:10px; font-size:13px;
  line-height:1.55; white-space:pre-wrap; word-break:break-word; }
.msg .who { font-size:11px; color:#8a94a6; display:block; margin-bottom:3px; }
.user { background:#233347; border:1px solid #2e425e; }
.asst { background:#22262f; border:1px solid #2e3340; }
.foot { color:#5b6472; font-size:11px; text-align:center; padding:4px 0 2px; }
"""


def _fmt_ms(ms):
    if ms is None:
        return "—"
    ms = int(ms)
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_progress_html(info):
    """把会话状态渲染成自包含 HTML。info 结构同 dsh_card.render_card。"""
    parts = ["<!doctype html><html><head><meta charset='utf-8'><style>" + _CSS + "</style></head><body><div class='wrap'>"]

    # header
    running = bool(info.get("running"))
    parts.append("<div class='head'>")
    parts.append("<p class='t'>DSH 任务进度</p>")
    parts.append("<div class='title'>" + _esc(info.get("title") or "（未命名会话）") + "</div>")
    badge = "<span class='badge run'>● 运行中</span>" if running else "<span class='badge idle'>○ 空闲</span>"
    meta = _esc(f"{info.get('provider','')}/{info.get('model','')}  ·  {info.get('session_id','')[:20]}")
    parts.append(f"<div class='meta'>{badge} &nbsp;{meta}</div>")
    parts.append("</div>")

    # stats
    st = info.get("stats") or {}
    if st:
        parts.append("<div class='card'><p class='sec'>📊 会话统计</p>")
        parts.append("<div class='stats'>" + _esc(
            f"轮次 {st.get('turns','—')} ｜ 步骤 {st.get('steps','—')} ｜ "
            f"LLM {_fmt_ms(st.get('llmMs'))} ｜ 输出 {st.get('decodeTokens','—')} tokens"
        ) + "</div></div>")

    # todos
    todos = info.get("todos") or []
    if todos:
        parts.append("<div class='card'><p class='sec'>✅ 任务清单</p>")
        for t in todos:
            done = str(t.get("status")) == "completed"
            cls = "todo done" if done else "todo"
            mark = "✔" if done else "○"
            parts.append(f"<div class='{cls}'>{mark} {_esc(t.get('content',''))}</div>")
        parts.append("</div>")

    # plan
    plan = info.get("plan") or {}
    if plan:
        parts.append("<div class='card'><p class='sec'>🗺️ 计划</p>")
        active = "已激活" if plan.get("active") else ("待定" if plan.get("pending") else "无")
        parts.append("<div class='stats'>计划状态：" + _esc(active) + "</div></div>")

    # context
    ctx = info.get("context") or {}
    if ctx:
        parts.append("<div class='card'><p class='sec'>🧠 上下文占用</p>")
        p = ctx.get("pressureTokens")
        win = ctx.get("contextWindow")
        pct = f"{p / win * 100:.1f}%" if isinstance(p, (int, float)) and win else "—"
        parts.append("<div class='stats'>已用 " + _esc(pct) + f"（{_esc(p or '—')} / {_esc(win or '—')} tokens）</div></div>")

    # messages
    msgs = info.get("messages") or []
    if msgs:
        parts.append("<div class='card'><p class='sec'>💬 最近对话</p>")
        for m in msgs[-6:]:
            role = m.get("role")
            cls = "msg user" if role == "user" else "msg asst"
            who = "👤 你" if role == "user" else "🤖 DSH"
            parts.append(f"<div class='{cls}'><span class='who'>{who}</span>{_esc(m.get('text',''))}</div>")
        parts.append("</div>")

    parts.append("<div class='foot'>dsh-wxauto · " + _esc(time.strftime("%Y-%m-%d %H:%M:%S")) + "</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def _estimate_height(info):
    """粗估内容高度（像素），用于窗口尺寸。"""
    h = 110
    if info.get("stats"):
        h += 70
    h += (len(info.get("todos") or []) * 26) + (60 if info.get("todos") else 0)
    if info.get("plan"):
        h += 70
    if info.get("context"):
        h += 70
    for m in (info.get("messages") or [])[-6:]:
        text = m.get("text", "")
        lines = max(1, len(text) // 38 + 1)
        h += 28 + lines * 22
    return h


def find_browser():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # PATH 兜底
    for name in ("msedge", "chrome", "chromium"):
        try:
            r = subprocess.run([name, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return name
        except Exception:
            continue
    return None


def render_progress_card(info, out_png, html_dir=None):
    """浏览器渲染：HTML → headless 截图 → PNG。无浏览器时回退 PIL 卡片。
    返回 PNG 路径。
    """
    browser = find_browser()
    if not browser:
        return render_pil_card(info, out_png)

    html_path = (html_dir or os.path.dirname(out_png)) + os.sep + os.path.basename(out_png) + ".html"
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_progress_html(info))

    height = min(2400, max(420, _estimate_height(info) + 80))
    url = "file:///" + html_path.replace("\\", "/")
    tmp_out = out_png + ".tmp.png"
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--window-size={WIDTH},{height}", f"--screenshot={tmp_out}", url,
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.3)
        if proc.poll() is None:
            proc.kill()
        # 等待截图文件落盘
        for _ in range(10):
            if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                break
            time.sleep(0.3)
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            os.replace(tmp_out, out_png)
            return out_png
    except Exception as e:
        print(f"[dsh_html_card] 浏览器截图失败：{e}", file=sys.stderr)
    finally:
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
            os.remove(html_path)
        except Exception:
            pass
    # 回退 PIL
    print("[dsh_html_card] 回退 PIL 卡片", file=sys.stderr)
    return render_pil_card(info, out_png)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dsh_ops import build_session_info
    from dsh_api import DshClient

    cfg = load_config()
    client = DshClient(base=cfg.get("dsh_base") or "http://127.0.0.1:3080")
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    if not sid:
        import json
        with open(resolve_path(cfg, "bridge_state", "data/bridge_state.json"), encoding="utf-8") as f:
            sid = json.load(f).get("active")
    info = build_session_info(client, sid)
    out = os.path.join(resolve_path(cfg, "shots_dir", "data/shots"), f"web_{sid.split('-')[-1]}_{time.strftime('%H%M%S')}.png")
    p = render_progress_card(info, out)
    print(p)
