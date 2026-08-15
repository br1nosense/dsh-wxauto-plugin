# -*- coding: utf-8 -*-
"""DSH 进度卡片 PNG 渲染：把会话状态（标题/状态/统计/任务清单/计划/最近消息）画成一张图。

这是「DSH 通过截图发送给用户告知进度」的实现：从 session.history 的 projections 与
消息事件渲染成图片，通过微信发送。纯本地渲染（PIL + 系统中文字体），不依赖浏览器。
"""
import datetime
import os

from PIL import Image, ImageDraw, ImageFont

# ---- 配色（深色主题，贴近 DSH web 暗色 UI） ----
BG = (24, 26, 32)
CARD = (32, 36, 45)
HEADER = (40, 46, 58)
TEXT = (226, 230, 238)
MUTED = (150, 158, 170)
ACCENT = (90, 190, 255)
GREEN = (96, 212, 132)
YELLOW = (240, 200, 90)
RED = (240, 120, 120)
USER_BG = (46, 62, 80)
ASST_BG = (40, 46, 58)
BORDER = (60, 66, 80)

PADDING = 22
LINE_H = 26
WIDTH = 720

_FONT_CACHE = {}


def _font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\Dengb.ttf" if bold else r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    font = None
    for c in candidates:
        if os.path.exists(c):
            try:
                font = ImageFont.truetype(c, size)
                break
            except Exception:
                font = None
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def wrap(draw, text, font, max_width):
    """按像素宽度逐字换行（适合中文）。返回行列表。"""
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        line = ""
        for ch in raw:
            probe = line + ch
            if draw.textlength(probe, font=font) <= max_width:
                line = probe
            else:
                lines.append(line)
                line = ch
        lines.append(line)
    return lines


def _fmt_ms(ms):
    if ms is None:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _truncate(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def render_card(info, out_path):
    """info 字段：
        title, session_id, running, provider, model,
        stats(dict), todos(list[{content,status}]), plan(dict),
        context(dict), messages(list[{role,text}])
    返回 out_path。
    """
    f_title = _font(20, bold=True)
    f_head = _font(15, bold=True)
    f_body = _font(14)
    f_small = _font(12)
    f_emoji = f_body

    # ---- 先测算内容高度 ----
    draw_probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))
    blocks = []  # (type, payload) 用于第二遍绘制

    def height_of():
        h = PADDING
        # header
        h += 30 + 8
        h += 24  # title
        h += 8 + 20  # meta
        h += 14
        # status line
        h += 22
        h += 10
        # stats section
        h += 22  # section head
        h += LINE_H
        h += 10
        # todos
        todos = info.get("todos") or []
        if todos:
            h += 22
            h += LINE_H * len(todos)
            h += 10
        # plan
        plan = info.get("plan") or {}
        if plan:
            h += 22
            h += LINE_H
            h += 10
        # context
        ctx = info.get("context") or {}
        if ctx:
            h += 22
            h += LINE_H
            h += 10
        # messages
        msgs = info.get("messages") or []
        if msgs:
            h += 22
            for m in msgs:
                role = m.get("role")
                text = m.get("text", "")
                prefix = "👤 你：" if role == "user" else "🤖 DSH："
                for line in wrap(draw_probe, prefix + text, f_body, WIDTH - 2 * PADDING - 16):
                    h += LINE_H + 4
                h += 8
        # footer
        h += 16
        return h

    height = height_of()
    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    y = PADDING
    # header 背景
    draw.rectangle([0, 0, WIDTH, 82], fill=HEADER)
    draw.text((PADDING, 14), "DSH 任务进度", font=f_title, fill=ACCENT)
    title = info.get("title") or "（未命名会话）"
    draw.text((PADDING, 46), _truncate(title, 42), font=f_body, fill=TEXT)
    y = 96

    # meta: 状态 badge + 模型 + session
    running = info.get("running")
    status_txt = "● 运行中" if running else "○ 空闲"
    status_col = GREEN if running else MUTED
    draw.text((PADDING, y), status_txt, font=f_head, fill=status_col)
    meta = f"{info.get('provider','')}/{info.get('model','')}  ·  {_truncate(info.get('session_id',''), 18)}"
    draw.text((PADDING, y + 22), meta, font=f_small, fill=MUTED)
    y += 60

    # stats
    st = info.get("stats") or {}
    if st:
        _section(draw, "📊 会话统计", PADDING, y, f_head, ACCENT)
        y += 30
        parts = [
            f"轮次 {st.get('turns','—')}",
            f"步骤 {st.get('steps','—')}",
            f"LLM 耗时 {_fmt_ms(st.get('llmMs'))}",
            f"输出 {st.get('decodeTokens','—')} tokens",
        ]
        draw.text((PADDING, y), "   ".join(parts), font=f_body, fill=TEXT)
        y += LINE_H + 12

    # todos
    todos = info.get("todos") or []
    if todos:
        _section(draw, "✅ 任务清单", PADDING, y, f_head, ACCENT)
        y += 30
        for t in todos:
            done = str(t.get("status")) == "completed"
            mark = "✔" if done else "○"
            col = GREEN if done else TEXT
            text = t.get("content", "")
            draw.text((PADDING, y), f"{mark} {_truncate(text, 44)}", font=f_body, fill=col)
            y += LINE_H
        y += 12

    # plan
    plan = info.get("plan") or {}
    if plan:
        _section(draw, "🗺️ 计划", PADDING, y, f_head, ACCENT)
        y += 30
        active = "已激活" if plan.get("active") else ("待定" if plan.get("pending") else "无")
        draw.text((PADDING, y), f"计划状态：{active}", font=f_body, fill=TEXT)
        y += LINE_H + 12

    # context
    ctx = info.get("context") or {}
    if ctx:
        _section(draw, "🧠 上下文占用", PADDING, y, f_head, ACCENT)
        y += 30
        p = ctx.get("pressureTokens")
        win = ctx.get("contextWindow")
        pct = f"{p / win * 100:.1f}%" if isinstance(p, (int, float)) and win else "—"
        draw.text((PADDING, y), f"已用 {pct}（{p or '—'} / {win or '—'} tokens）", font=f_body, fill=TEXT)
        y += LINE_H + 12

    # messages
    msgs = info.get("messages") or []
    if msgs:
        _section(draw, "💬 最近对话", PADDING, y, f_head, ACCENT)
        y += 30
        for m in msgs[-6:]:
            role = m.get("role")
            text = m.get("text", "")
            if role == "user":
                prefix = "👤 "
                bubble = USER_BG
                name = "你"
            else:
                prefix = "🤖 "
                bubble = ASST_BG
                name = "DSH"
            lines = wrap(draw, prefix + text, f_body, WIDTH - 2 * PADDING - 16)
            draw.rounded_rectangle(
                [PADDING - 4, y - 2, WIDTH - PADDING + 4, y + len(lines) * (LINE_H + 4) + 4],
                radius=8, fill=bubble, outline=BORDER)
            draw.text((PADDING, y), f"{prefix}{name}", font=f_small, fill=MUTED)
            y += 20
            for line in lines:
                draw.text((PADDING, y), line, font=f_body, fill=TEXT)
                y += LINE_H + 4
            y += 8

    # footer
    draw.line([PADDING, height - 30, WIDTH - PADDING, height - 30], fill=BORDER)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((PADDING, height - 24), f"dsh-wxauto · 生成于 {now}", font=f_small, fill=MUTED)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _section(draw, title, x, y, font, color):
    draw.text((x, y), title, font=font, fill=color)
