# -*- coding: utf-8 -*-
"""dsh-wxauto 公共模块：路径、配置、输出编码与微信实例封装。

所有脚本均基于 wxauto4（免费版）：
  - 发送：SendMsg / SendFiles
  - 会话/读取：GetSession / ChatWith / GetAllMessage / GetMyInfo / ChatInfo
监听使用轮询方案（免费版无 AddListenChat/GetNextNewMessage，那是 Plus 版 wxautox4 专属）。
"""
import argparse
import json
import os
import re
import sys
import time

if sys.platform == "win32":
    import msvcrt
else:  # 非 Windows 兜底：用不持锁（wxauto 本就仅支持 Windows）
    msvcrt = None

# 强制 UTF-8 输出，避免中文在 harness/控制台乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG_PATH = os.path.join(SKILL_ROOT, "config.json")
DATA_DIR = os.path.join(SKILL_ROOT, "data")

# 跨进程微信 UI 互斥锁文件：所有操作微信窗口的进程（桥/监听/发送）共享同一把锁，
# 避免 UIA 抢占导致「发送假成功」或「抢鼠标」。锁文件放在技能 data 目录，
# 通过 junction 两端（插件目录 / .dsh/skills）指向同一路径。
UI_LOCK_PATH = os.path.join(DATA_DIR, "wx_ui.lock")


class UI_Lock:
    """跨进程微信 UI 互斥锁（Windows 锁文件 + msvcrt.locking，纯标准库）。

    用法：with UI_Lock(): ...任何 wxauto UI 操作...

    要点：
      - 同一时刻只允许一个进程执行微信 UI 操作（wxauto4 非线程/进程安全）。
      - 超时自动放弃并抛 TimeoutError，避免无限等待。
      - 进程崩溃时 OS 自动释放锁，不会死锁。
    """

    def __init__(self, path=None, timeout=30.0):
        self.path = path or UI_LOCK_PATH
        self.timeout = timeout
        self._fh = None

    def acquire(self, timeout=None):
        if msvcrt is None:  # 非 Windows：不持锁（功能受限但至少不报错）
            return self
        timeout = self.timeout if timeout is None else timeout
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + timeout
        while True:
            fh = None
            try:
                fh = open(self.path, "a+b")
                # 确保文件至少有 1 字节可锁
                fh.seek(0, os.SEEK_END)
                if fh.tell() < 1:
                    fh.write(b"\x00")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                self._fh = fh
                return self
            except OSError:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"微信 UI 正被其他进程占用（等待超时 {int(timeout)}s）。"
                        f"若有残留桥/监听进程，请先关闭 wxauto 开关或结束 python 进程。"
                    )
                time.sleep(0.2)
            except Exception:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                raise

    def release(self):
        if self._fh is not None:
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


class SingleInstance:
    """进程级单实例锁：整个进程生命周期持有（防止桥/监听多开导致重复受理）。

    与 UI_Lock（每次操作短时持有）不同，本锁在进程启动时获取、随进程退出自动释放
    （msvcrt 文件锁随句柄/进程关闭即释放，崩溃也不残留）。第二个进程获取会失败。
    用途：wx_bridge/wx_listen 共用同一把锁，同一时间只允许一个 wxauto 守护进程，
    避免「插件自动拉起 + 手动 wx-bridge.ps1 看护」等多开 → 同一条微信消息被
    多个进程轮询并重复受理/重复推送。
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(DATA_DIR, "wx_daemon.lock")
        self._fh = None

    def acquire(self, timeout=0.2):
        if msvcrt is None:  # 非 Windows：不强制单实例（wxauto 本就不支持）
            return True
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + timeout
        while True:
            fh = None
            try:
                fh = open(self.path, "a+b")
                fh.seek(0, os.SEEK_END)
                if fh.tell() < 1:
                    fh.write(b"\x00")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                self._fh = fh
                return True
            except OSError:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                if time.time() >= deadline:
                    return False
                time.sleep(0.1)
            except Exception:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                raise

    def release(self):
        if self._fh is not None:
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._fh = None

DEFAULT_CONFIG = {
    "python": "py -3",
    "target_chats": ["文件传输助手"],
    "listen_chats": ["文件传输助手"],
    "listen_interval": 3,
    "listen_log": "data/listen.jsonl",
    "reply_rules_file": "data/reply_rules.json",
    "auto_report": True,
    "report_prefix": "[DSH]",
    "report_tail": "\n—— DSH 自动汇报",
    "dsh_base": "http://127.0.0.1:3080",
    "dsh_cwd": "",
    "bridge_chats": [],
    "bridge_poll_interval": 8,
    "bridge_task_timeout": 900,
    "bridge_enabled": False,
    "listen_enabled": False,
    "enabled": False,
    "bridge_seen": "data/bridge_seen.json",
    "bridge_pending": "data/bridge_pending.json",
    "bridge_force_interval": 30,
    # 群聊策略：白名单 + @ 响应（P0）
    "group_whitelist": [],
    "group_mention_only": True,
    "my_aliases": [],
}

# DSH 设置（插件写 data/dsh_settings.json，schema 字段 camelCase）→ 本技能 config 字段（snake_case）
DSH_SETTINGS_MAP = {
    "dshBase": "dsh_base",
    "cwd": "dsh_cwd",
    "targetChats": "target_chats",
    "listenChats": "listen_chats",
    "bridgeChats": "bridge_chats",
    "listenInterval": "listen_interval",
    "taskTimeout": "bridge_task_timeout",
    "autoReport": "auto_report",
    "reportPrefix": "report_prefix",
    "reportTail": "report_tail",
    "enabled": "enabled",            # 总开关（单一 switch）
    "bridgeEnabled": "bridge_enabled",  # 旧字段兼容
    "listenEnabled": "listen_enabled",  # 旧字段兼容
    "groupWhitelist": "group_whitelist",    # 群聊白名单
    "groupMentionOnly": "group_mention_only",  # 群聊仅响应 @ 我
    "myAliases": "my_aliases",              # 我的昵称别名（@ 检测用）
}


def load_config():
    """读取 config.json，叠加 settings.yaml 的 wxauto 段，再叠加插件镜像（最高优先）。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update(user)
    except FileNotFoundError:
        pass
    except Exception as e:  # 配置损坏不致命，回退默认
        print(f"[wx_common] 警告：读取 {CONFIG_PATH} 失败，使用默认配置：{e}", file=sys.stderr)

    # 直读 DSH 设置文档（~/.dsh/settings.yaml 的 wxauto: 段）——不依赖插件镜像，重启前也能用
    try:
        import yaml
        dsh_home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
        with open(os.path.join(dsh_home, "settings.yaml"), "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        wxauto_section = doc.get("wxauto") if isinstance(doc, dict) else None
        if isinstance(wxauto_section, dict):
            for k, v in DSH_SETTINGS_MAP.items():
                if k in wxauto_section and wxauto_section[k] is not None:
                    cfg[v] = wxauto_section[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[wx_common] 警告：读取 settings.yaml 失败：{e}", file=sys.stderr)

    # 插件镜像设置覆盖（camelCase → snake_case）；utf-8-sig 兼容带 BOM 的写入
    dsh_json = os.path.join(SKILL_ROOT, "data", "dsh_settings.json")
    try:
        with open(dsh_json, "r", encoding="utf-8-sig") as f:
            mirror = json.load(f)
        if isinstance(mirror, dict):
            for k, v in DSH_SETTINGS_MAP.items():
                if k in mirror and mirror[k] is not None:
                    cfg[v] = mirror[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[wx_common] 警告：读取 {dsh_json} 失败：{e}", file=sys.stderr)
    return cfg


def resolve_path(cfg, key, default_rel):
    """把配置里的相对路径（相对技能根目录）解析为绝对路径。"""
    raw = cfg.get(key) or default_rel
    if not os.path.isabs(raw):
        raw = os.path.join(SKILL_ROOT, raw)
    return os.path.normpath(raw)


# ---- 群聊识别与 @ 响应策略（P0） ----
# wxauto4 中 ChatInfo()/chat_info()['chat_type']：好友 'friend'、群聊 'group'、
# 客服 'service'、公众号 'official'。4.x 群消息的 sender 只是发送人显示名
# （不含群名前缀），因此以 chat_type 为准，不能靠冒号判断。
GROUP_CHAT_TYPES = {"group", "群聊"}


def is_group_chat(rec):
    """判断消息记录是否来自群聊。

    以 chat_type 为准（'group' 为群聊）；chat_type 缺失时兜底按 3.x 的
    「群名:昵称」sender 风格判断。
    """
    ctype = str((rec or {}).get("chat_type") or "")
    if ctype:
        return ctype in GROUP_CHAT_TYPES
    sender = str((rec or {}).get("sender") or "")
    return ":" in sender or "：" in sender


def mentioned_me(content, aliases, at_list=None):
    """判断群消息是否 @ 了我：内容中出现 @昵称（微信把 @ 渲染成文本，实测有效）。

    at_list 通道已废弃（FriendMessage.at 是 UI 动作，见 capture_at），保留参数
    仅为兼容。aliases 取 resolve_my_aliases 的结果（GetMyInfo 昵称 + 配置
    my_aliases）。别名缺省为空时返回 False（无法判断 → 不响应，安全方向）。
    """
    if not aliases:
        return False
    content = content or ""
    for alias in aliases:
        alias = (alias or "").strip()
        if not alias:
            continue
        if re.search(r"@\s*" + re.escape(alias), content):
            return True
    for name in (at_list or []):
        name = str(name or "").strip()
        if name and name in aliases:
            return True
    return False


def capture_at(m):
    """（废弃通道）提取消息中被 @ 的人名列表。

    实测（wxauto4 41.1.2）：FriendMessage.at 是「UI 操作动作」而非获取器——
    at(content) 返回 WxResponse（status: 成功），可能触发微信 UI 操作，有副作用，
    不能在生产代码里调用。@ 检测请完全依赖内容（content 里含 "@昵称" 文本，
    实测 '@白无意\\u2005今天天气怎么样'）。本函数恒返回 [] 以保留调用点兼容。
    """
    return []


def strip_mention_prefix(content, aliases):
    """去掉消息开头的 @我的昵称（仅精确匹配别名，最多剥 3 个），返回剩余内容。

    群聊里「@机器人 /list」「@机器人 1」这类消息，剥掉开头 @ 前缀后命令/回答
    才能被正确解析。只剥精确匹配别名的 @，避免误删 @别人的内容。
    """
    c = content or ""
    names = {a.strip() for a in (aliases or []) if a and a.strip()}
    for _ in range(3):
        m = re.match(r"^\s*@\s*([^\s@]+)\s*", c)
        if not m or m.group(1) not in names:
            break
        c = c[m.end():]
    return c.strip()


def resolve_my_aliases(wx, configured=None):
    """解析「我」的昵称别名列表（群聊 @ 检测用）。

    配置 my_aliases 优先，再补 GetMyInfo() 与 wx.nickname（自动去重）。
    拿不到昵称时可能返回空列表（@ 检测不可用，调用方应告警）。
    """
    aliases = []
    seen = set()
    for a in (configured or []):
        a = str(a).strip()
        if a and a not in seen:
            seen.add(a)
            aliases.append(a)
    try:
        info = wx.GetMyInfo() or {}
        # 实测（wxauto4 41.1.2）：GetMyInfo 返回 {'display_name': 昵称, 'id': ..., 'region': ...}
        for k in ("display_name", "name", "nickname", "nick"):
            v = info.get(k)
            if v and str(v).strip() and str(v).strip() not in seen:
                seen.add(str(v).strip())
                aliases.append(str(v).strip())
    except Exception:
        pass
    try:
        nick = getattr(wx, "nickname", None)
        if nick and str(nick).strip() and str(nick).strip() not in seen:
            seen.add(str(nick).strip())
            aliases.append(str(nick).strip())
    except Exception:
        pass
    return aliases


def out_json(obj):
    """输出标准 JSON（供 harness 自动解析）。"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def out_text(lines):
    """输出人类可读文本。lines 为字符串列表或单个字符串。"""
    if isinstance(lines, str):
        lines = [lines]
    print("\n".join(str(x) for x in lines))


def wx_init(debug=False):
    """创建微信实例并返回。import 放函数内，便于 wx_test 做依赖探测。

    整个构造过程持有跨进程 UI 锁：桥/监听可能几乎同时启动，
    两个 wxauto 实例并发枚举微信窗口会互相冲突（曾出现
    'unhashable type: list' 瞬态错误）。加锁后两者串行初始化。

    resize=False：不自动调整微信窗口尺寸/激活前台——每次 init 若 resize
    会反复重置窗口并抢焦点（"抢鼠标"主因之一）。
    """
    with UI_Lock():
        from wxauto4 import WeChat

        return WeChat(debug=debug, resize=False, ads=False)


def safe_send(wx, who, msg=None, filepath=None, exact=True, at=None, retries=2):
    """向 who 发送文本或文件，返回 (ok, text)。

    优先 ChatWith 精确切换（exact=True），再用 ChatInfo 校验窗口名一致。
    若窗口不匹配，等待后重试；重试仍不匹配则拒绝发送（绝不误发到别的聊天）。

    整个发送过程持有跨进程 UI 锁（UI_Lock），与桥/监听互斥，
    避免并发抢占微信窗口导致「发送假成功」（上报成功但消息未落地）。
    """
    def _chat_info():
        try:
            return wx.ChatInfo() or {}
        except Exception:
            return {}

    with UI_Lock():
        for attempt in range(retries + 1):
            wx.ChatWith(who=who, exact=exact)
            time.sleep(0.4)  # 等待窗口切换/搜索完成
            info = _chat_info()
            actual = info.get("chat_name")
            if actual == who:
                break
            if attempt < retries:
                continue
            return False, f"窗口不匹配（重试{retries}次后）：期望[{who}]，实际[{actual}]，已取消发送"
        if filepath:
            # 同时带文字+文件时：先发文字再发文件（旧的 SendFiles 分支会丢弃 msg）
            if msg:
                try:
                    resp = wx.SendMsg(msg=msg, who=who, clear=True, at=at, exact=exact)
                    if isinstance(resp, dict) and str(resp.get("status")) != "成功":
                        print(f"[wx] 先发文字失败（继续发文件）：{resp}", file=sys.stderr)
                except Exception as e:
                    print(f"[wx] 先发文字失败（继续发文件）：{e}", file=sys.stderr)
            resp = wx.SendFiles(filepath=filepath, who=who, exact=exact)
        else:
            resp = wx.SendMsg(msg=msg, who=who, clear=True, at=at, exact=exact)
        if isinstance(resp, dict):
            # WxResponse 是 dict，键为 status/message/data；status=='成功' 才算成功
            ok = str(resp.get("status")) == "成功"
            return ok, str(resp.get("message") or resp)
        return True, str(resp)


def add_common_args(parser):
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--debug", action="store_true", help="wxauto4 调试日志")
    return parser


def main_wrapper(fn):
    """脚本入口包装：捕获异常并给出友好提示。"""
    try:
        return fn()
    except KeyboardInterrupt:
        print("[wx] 已中断", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[wx] 错误：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
