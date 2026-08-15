/**
 * @dsh-user/dsh-wxauto — 浏览器半边（client bundle）
 *
 * 在 DSH 设置页（设置 → 插件）注册「微信自动化」卡片，绑定 `wxauto` 设置命名空间：
 *   - 双向桥开关 bridgeEnabled（开启=自动拉起 wx_bridge.py，占用微信窗口）
 *   - 控制聊天 / 汇报目标 / 监听聊天 / DSH 地址 / 轮询间隔 / 任务超时等
 * 保存时按字段差异写 settings（scope.set/unset，revision 校验由 Host 负责）。
 *
 * 手写 __ModuleLoader__ 包格式：只 require 平台 seed 词 react，其余能力
 * （slots / settingsScope）经 ctx.get() 运行时读取；全程 try/catch，任何失败
 * 只降级为不显示卡片，绝不拖垮浏览器启动。
 */
window.__ModuleLoader__.load({
  id: "@dsh-user/dsh-wxauto",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    const React = require("react");
    const { useSyncExternalStore, useState } = React;

    const NS = "wxauto";
    let scope = null; // 绑定后的 SettingsScope，apply() 里赋值

    // ── 字段定义 ──────────────────────────────────────────────────────
    const FIELDS = [
      { key: "enabled", label: "总开关", type: "bool", hint: "开启=启动一体化守护（微信⇄DSH 桥+监听+自动回复+进度推送）；关闭=灭杀所有相关进程。运行期间占用本机微信窗口" },
      { key: "bridgeChats", label: "控制聊天", type: "list", hint: "守护进程轮询的聊天（逗号分隔，如：工作群,我的微信）" },
      { key: "targetChats", label: "汇报目标聊天", type: "list", hint: "任务进度发送目标（@config 时使用）" },
      { key: "listenChats", label: "监听聊天（旧）", type: "list", hint: "兼容旧字段，已并入总开关，一般无需填写" },
      { key: "dshBase", label: "DSH web 地址", type: "text", hint: "回环 RPC 地址" },
      { key: "listenInterval", label: "轮询间隔（秒）", type: "num", hint: "" },
      { key: "taskTimeout", label: "任务等待超时（秒）", type: "num", hint: "" },
      { key: "autoReport", label: "任务完成自动汇报", type: "bool", hint: "agent 按技能约定在任务完成时推送微信" },
      { key: "reportPrefix", label: "汇报前缀", type: "text", hint: "@config 批量汇报时的前缀" },
      { key: "groupWhitelist", label: "群聊白名单", type: "list", hint: "允许响应的群聊（逗号分隔）。白名单内的群自动加入监听，无需再填控制聊天；留空=不限制" },
      { key: "groupMentionOnly", label: "群聊仅响应 @ 我", type: "bool", hint: "开启后群聊里只有 @ 你的消息才受理（命令/自动回复/任务）；回答桥提问不受限" },
      { key: "myAliases", label: "我的昵称别名", type: "list", hint: "@ 检测用（群名片等）。留空自动取 GetMyInfo 昵称" },
    ];

    // ── Switch 滑块开关（总开关 / 自动汇报等 bool 字段）────────────────
    function SwitchControl({ checked, onChange, disabled }) {
      const on = Boolean(checked);
      const track = {
        position: "relative", display: "inline-block", width: 40, height: 22,
        borderRadius: 11, background: on ? "#2b5cff" : "#3c4250",
        boxShadow: on ? "0 0 0 1px rgba(43,92,255,.55)" : "inset 0 0 0 1px #2a2f3b",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        transition: "background .18s ease, box-shadow .18s ease",
        verticalAlign: "middle", flexShrink: 0,
      };
      const knob = {
        position: "absolute", top: 3, left: on ? 21 : 3, width: 16, height: 16,
        borderRadius: "50%", background: "#fff",
        boxShadow: "0 1px 2px rgba(0,0,0,.35)",
        transition: "left .18s ease",
      };
      return React.createElement("div", {
        role: "switch",
        "aria-checked": on,
        "aria-disabled": Boolean(disabled),
        style: track,
        onClick: (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!disabled && onChange) onChange(!on);
        },
      }, React.createElement("div", { style: knob }));
    }

    function parseList(text) {
      return text.split(/[,，;；]/).map((s) => s.trim()).filter(Boolean);
    }

    function WxautoSection() {
      const [draft, setDraft] = useState(null);
      const snapshot = useSyncExternalStore(
        (cb) => scope.subscribe(cb),
        () => scope.getSnapshot()
      );
      if (!scope) return null;
      const value = snapshot.value || {};
      const current = draft || value;
      const dirty = draft !== null;

      const edit = (key, next) => setDraft({ ...current, [key]: next });

      const save = async () => {
        const norm = (x) => (Array.isArray(x) ? x.join("\u0000") : String(x ?? ""));
        for (const f of FIELDS) {
          const prev = value[f.key];
          const next = current[f.key];
          if (norm(prev) === norm(next)) continue;
          try {
            if (next === undefined || next === null || next === "" || (Array.isArray(next) && next.length === 0)) {
              await scope.unset(f.key);
            } else {
              await scope.set(f.key, next);
            }
          } catch (e) {
            console.warn("[dsh-wxauto] 设置写入失败", f.key, e);
          }
        }
        setDraft(null);
      };

      const unavailable = snapshot.status === "unavailable";
      const statusText = unavailable
        ? "命名空间不可用（需重启 dsh web 且 apiproxy 暴露 wxauto）"
        : snapshot.status === "loading" ? "加载中…" : snapshot.writable ? "" : "（只读）";

      const inputFor = (f) => {
        const val = current[f.key];
        const style = { width: "100%", padding: "4px 6px", boxSizing: "border-box", background: "#1c1e26", color: "#e2e6ee", border: "1px solid #3c4250", borderRadius: 6 };
        if (f.type === "bool") {
          return React.createElement(SwitchControl, {
            checked: Boolean(val),
            disabled: !snapshot.writable,
            onChange: (v) => edit(f.key, v),
          });
        }
        const text = f.type === "list" ? (Array.isArray(val) ? val.join(", ") : "") : String(val ?? "");
        return React.createElement("input", {
          type: "text", value: text, disabled: !snapshot.writable, style,
          onChange: (e) => {
            const t = e.target.value;
            edit(f.key, f.type === "list" ? parseList(t) : f.type === "num" ? (Number.isFinite(Number(t)) ? Number(t) : undefined) : t);
          }
        });
      };

      const warn = current.enabled
        ? React.createElement("p", { style: { margin: "0 0 10px", fontSize: 13, color: "#f08a6a" } },
            "⚠️ 已开启：本机微信窗口将被自动化占用，期间请勿手动操作微信。关闭总开关即灭杀所有进程。")
        : null;

      return React.createElement("div", { style: { padding: "12px 16px" } },
        React.createElement("p", { style: { margin: "0 0 8px", fontSize: 13, color: "#9aa3b2" } },
          "基于 wxauto4 的微信自动化。修改后点「保存」生效（双向桥开关实时起停）。"),
        warn,
        ...FIELDS.map((f) =>
          React.createElement("label", { key: f.key, style: { display: "block", margin: "6px 0" } },
            React.createElement("span", { style: { display: "inline-block", width: 150, fontSize: 13, color: "#e2e6ee" } }, f.label),
            inputFor(f),
            f.hint ? React.createElement("span", { style: { display: "block", fontSize: 12, color: "#8892a2", marginLeft: 158 } }, f.hint) : null
          )
        ),
        React.createElement("div", { style: { marginTop: 10, display: "flex", gap: 8, alignItems: "center" } },
          React.createElement("button", {
            onClick: save, disabled: !dirty || !snapshot.writable,
            style: { padding: "5px 14px", borderRadius: 6, border: "1px solid #3c4250", background: dirty ? "#2b5cff" : "#262b36", color: "#e2e6ee", cursor: dirty ? "pointer" : "default" }
          }, "保存"),
          React.createElement("span", { style: { fontSize: 12, color: "#8892a2" } }, statusText)
        )
      );
    }

    // ── 插件主体 ───────────────────────────────────────────────────────
    function apply(ctx) {
      try {
        ctx.effect(() => {
          const timer = setInterval(() => {
            try {
              const slots = ctx.get("slots");
              const settingsScope = ctx.get("settingsScope");
              if (!slots || !settingsScope) return;
              scope = settingsScope.bind({ namespace: NS });
              // 独立设置 Tab：位于「换肤与宠物」(order 100) 下方
              slots.inject("settings.section", () => slots.register(
                { name: "settings.section", id: "dsh-wxauto", order: 110, label: () => "微信自动化" },
                WxautoSection
              ));
              clearInterval(timer);
            } catch (e) {
              ctx.logger?.warn?.("dsh-wxauto: 设置 Tab 注册失败", e);
            }
          }, 300);
          return () => clearInterval(timer);
        }, "dsh-wxauto: settings section boot");
      } catch (e) {
        ctx.logger?.warn?.("dsh-wxauto: client boot failed", e);
      }
    }

    exports.apply = apply;
    return module.exports;
  }
});
