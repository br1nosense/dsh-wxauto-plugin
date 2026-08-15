/**
 * @dsh-user/dsh-wxauto — DSH 微信自动化插件（host 半边）
 *
 * 职责：
 *   1. 把本包携带的 `dsh-wxauto` 技能注册进 ctx.skills（运行时注册）。
 *   2. 注册 `wxauto` 设置命名空间（installSettingsSection）→ 配置落在
 *      `~/.dsh/settings.yaml` 的 `wxauto:` 段，热重载，可在 DSH 设置页编辑
 *      （需 apiproxy 暴露该命名空间，见 install.ps1 的 pnpm patch）。
 *   3. 双向桥开关 `wxauto.bridge.enabled`：开启时自动拉起 wx_bridge.py，
 *      关闭时杀掉；运行期间微信窗口被自动化占用（占用提示写入桥日志）。
 *   4. 把解析后的配置镜像到 `skills/dsh-wxauto/data/dsh_settings.json`，
 *      Python 侧（wx_common.load_config）以此为最高优先层。
 *   5. 提供 `ctx.wxauto` 服务（描述性接口：开关/桥状态/配置）。
 */
import { spawn, spawnSync } from "node:child_process";
import { closeSync, createWriteStream, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import z from "@deepseek-ai/schemastery";
import { installSettingsSection, settingsNamespace } from "@deepseek-ai/dsh-settings";

const PKG_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SKILL_DIR = join(PKG_ROOT, "skills", "dsh-wxauto");
const SKILL_FILE = join(SKILL_DIR, "SKILL.md");
const DATA_DIR = join(SKILL_DIR, "data");
const SETTINGS_JSON = join(DATA_DIR, "dsh_settings.json");
const BRIDGE_SCRIPT = join(SKILL_DIR, "scripts", "wx_bridge.py");
const LISTEN_SCRIPT = join(SKILL_DIR, "scripts", "wx_listen.py");
const BRIDGE_LOG = join(DATA_DIR, "bridge.log");
const SKILL_NAME = "dsh-wxauto";
const SKILL_DESCRIPTION =
  "基于 wxauto4（Python，免费版）的微信自动化技能：DSH 任务进度推送到微信、消息监听" +
  "（记录+关键词自动回复）、微信⇄DSH 双向桥（/new /list /switch /status /shot 等命令，" +
  "驱动 DSH 跑任务并回传进度截图）。双向桥在 DSH 设置（wxauto → bridge.enabled）开关。";

/** `wxauto` 设置命名空间的 schema（默认值 = 组合层；字段扁平，便于设置页与 Python 读取）。 */
const WxautoSchema = z.object({
  dshBase: z.string().default("http://127.0.0.1:3080"),
  cwd: z.string().default(process.env.USERPROFILE ?? process.env.HOME ?? process.cwd()),
  targetChats: z.array(z.string()).default([]),
  listenChats: z.array(z.string()).default([]),
  bridgeChats: z.array(z.string()).default([]),
  listenInterval: z.number().default(3),
  taskTimeout: z.number().default(900),
  autoReport: z.boolean().default(true),
  reportPrefix: z.string().default("[DSH]"),
  reportTail: z.string().default(""),
  /** 群聊策略（P0）：白名单 + @ 响应。群聊消息需在白名单内且（开启时）@ 我才受理。 */
  groupWhitelist: z.array(z.string()).default([]),
  groupMentionOnly: z.boolean().default(true),
  myAliases: z.array(z.string()).default([]),
  /** 总开关：开启=启动一体化守护进程（桥+监听），关闭=灭杀所有相关进程。 */
  enabled: z.boolean().default(false),
  /** 旧字段（兼容历史 settings）：bridgeEnabled/listenEnabled → enabled。 */
  bridgeEnabled: z.boolean(),
  listenEnabled: z.boolean(),
});

/** 从 SKILL.md 剥掉 frontmatter 只留正文。 */
function stripFrontmatter(markdown) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(markdown);
  return m ? markdown.slice(m[0].length) : markdown;
}

let _python = null;
/**
 * 显式解析 Python 解释器（不依赖 dsh web 进程的 PATH）：
 * 优先 DSH_WX_PYTHON 环境变量，其次 py/python/python3，再探测常见绝对路径。
 * 用 spawnSync 校验可执行且版本为 Python 3。
 */
function resolvePython() {
  if (_python) return _python;
  const candidates = [];
  if (process.env.DSH_WX_PYTHON) candidates.push(process.env.DSH_WX_PYTHON.trim().split(/\s+/));
  candidates.push(["py", "-3"], ["python"], ["python3"]);
  if (process.platform === "win32" && process.env.USERPROFILE) {
    const launcher = join(process.env.USERPROFILE, "AppData", "Local", "Programs", "Python", "Launcher", "py.exe");
    candidates.push([launcher, "-3"]);
    for (const ver of ["313", "312", "311", "310", "39"]) {
      candidates.push([join(process.env.USERPROFILE, "AppData", "Local", "Programs", "Python", `Python${ver}`, "python.exe")]);
    }
  }
  for (const cand of candidates) {
    try {
      const r = spawnSync(cand[0], [...cand.slice(1), "--version"], { timeout: 5000, windowsHide: true });
      const text = String(r.stdout || "") + String(r.stderr || "");
      if (r.status === 0 && /Python 3\.\d+/.test(text)) {
        _python = cand;
        return cand;
      }
    } catch { /* 尝试下一个 */ }
  }
  _python = ["py", "-3"]; // 兜底；spawn 失败时会写入桥日志
  return _python;
}

export default function dshWxauto(ctx, entry = {}) {
  // ── 1) 注册技能 ──────────────────────────────────────────────────
  let source;
  try {
    source = stripFrontmatter(readFileSync(SKILL_FILE, "utf8"));
  } catch (error) {
    ctx.logger?.warn?.(`[dsh-wxauto] 读取技能文件失败：${error?.message ?? error}`);
    source = `# dsh-wxauto\n\n技能文件缺失：${SKILL_FILE}`;
  }
  ctx.skills.register({
    name: SKILL_NAME,
    description: SKILL_DESCRIPTION,
    whenToUse:
      "用户要求把 DSH 任务进度/结果发到微信、监听微信消息、或用微信反向操作 DSH" +
      "（切换/新建对话、驱动任务、查看进度截图），或询问微信桥开关/状态时。",
    source,
    resourceBase: { kind: "directory", path: SKILL_DIR },
    metadata: { version: "0.3.0", repository: "https://github.com/cluic/wxauto" },
  });

  // ── 2) 设置命名空间 + 桥进程管理 ──────────────────────────────────
  let resolved = null;
  let bridgeChild = null;
  let bridgeState = "stopped"; // stopped | starting | running | failed
  let settingsService = null;
  let bridgeWanted = false;
  let bridgeMode = "full"; // full=桥（命令/任务+推送）；listen=纯监听（记录+自动回复）
  let bridgeRetryTimer = null;
  let listenChild = null;
  let listenState = "stopped"; // stopped | starting | running | failed
  let listenWanted = false;
  let listenRetryTimer = null;
  let syncTimer = null;

  function writeSettingsJson(cfg) {
    try {
      mkdirSync(DATA_DIR, { recursive: true });
      writeFileSync(SETTINGS_JSON, JSON.stringify(cfg, null, 2), "utf8");
    } catch (error) {
      ctx.logger?.warn?.(`[dsh-wxauto] 写 dsh_settings.json 失败：${error?.message ?? error}`);
    }
  }

  function logBridge(line) {
    try {
      mkdirSync(DATA_DIR, { recursive: true });
      const stream = createWriteStream(BRIDGE_LOG, { flags: "a" });
      stream.write(`[${new Date().toISOString()}] ${line}\n`);
      stream.end();
    } catch { /* 日志失败不影响主流程 */ }
  }

  function clearBridgeRetry() {
    if (bridgeRetryTimer) { clearTimeout(bridgeRetryTimer); bridgeRetryTimer = null; }
  }

  function stopBridge(reason) {
    clearBridgeRetry();
    if (bridgeChild) {
      // Windows 下 py.exe 是启动器，会再拉起真正的 python.exe 子进程；
      // 只 kill 父进程（py.exe）不会杀掉 python.exe，后者会带着 wxauto 继续轮询，
      // 表现为「关掉开关后仍占用微信窗口/抢鼠标」。这里用 taskkill /T 杀整棵进程树。
      const pid = bridgeChild.pid;
      try {
        if (process.platform === "win32") {
          spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
        } else {
          bridgeChild.kill();
        }
      } catch (error) {
        ctx.logger?.warn?.(`[dsh-wxauto] 停止双向桥进程树失败（pid=${pid}）：${error?.message ?? error}`);
      }
      bridgeChild = null;
      bridgeState = "stopped";
      ctx.logger?.info?.("[dsh-wxauto] 双向桥已停止" + (reason ? `（${reason}）` : ""));
    } else if (bridgeState !== "stopped") {
      bridgeState = "stopped";
    }
  }

  /** 灭杀所有 wxauto 相关进程（含手动启动/残留的 wx_bridge.py / wx_listen.py），
   *  用于总开关关闭与插件卸载。 */
  function killAllWxauto(reason) {
    stopBridge(reason);
    try {
      const ps = [
        "-NoProfile", "-NonInteractive", "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(py|python)' -and $_.CommandLine -match 'wx_(bridge|listen)\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
      ];
      const r = spawnSync("powershell", ps, { windowsHide: true, timeout: 20000 });
      ctx.logger?.info?.(`[dsh-wxauto] 已灭杀残留 wxauto 进程${reason ? `（${reason}）` : ""}${r.status === 0 ? "" : `（sweep 退出码 ${r.status}）`}`);
    } catch (error) {
      ctx.logger?.warn?.(`[dsh-wxauto] 灭杀残留 wxauto 进程失败：${error?.message ?? error}`);
    }
  }

  function startBridge(cfg) {
    clearBridgeRetry();
    // 幂等：已有子进程或正在启动则不再拉起（防止 onChange 重复触发）
    if (bridgeChild || bridgeState === "starting") return;
    // 先清扫残留的 wxauto 进程（防止与手动起/崩溃残留的双开，双进程=双倍抢鼠标）
    killAllWxauto("启动前清扫");
    // 桥+监听已整合为同一进程：full 模式用 bridgeChats，listen 模式用 listenChats
    const mode = bridgeMode || (cfg.bridgeEnabled ? "full" : "listen");
    const chats = (mode === "listen" ? (cfg.listenChats ?? []) : (cfg.bridgeChats ?? [])).join(",");
    bridgeState = "starting";
    logBridge(
      `⚠️ 一体化守护启动（模式=${mode}）。运行期间将占用本机微信窗口，` +
      "请勿手动操作微信；完成后在 DSH 设置关闭对应开关即停止。"
    );
    const py = resolvePython();
    // 用 fs.openSync 拿到真实 fd 再传给 stdio：createWriteStream 的 fd 在文件打开前是 null，
    // 直接把它传给 spawn 的 stdio 会抛 "The argument 'stdio' is invalid"
    // （这正是之前「开关打开后桥毫无反应」的原因——startBridge 每次都启动失败）。
    let outFd = "ignore";
    try {
      outFd = openSync(BRIDGE_LOG, "a");
    } catch { /* 打不开日志文件就把进程输出丢弃 */ }
    try {
      bridgeChild = spawn(
        py[0],
        [...py.slice(1), BRIDGE_SCRIPT, "--mode", mode, ...(chats ? ["--who", chats] : [])],
        {
          cwd: SKILL_DIR,
          stdio: ["ignore", outFd, outFd],
          windowsHide: true,
        }
      );
      bridgeState = "running";
      bridgeChild.on("exit", (code) => {
        if (typeof outFd === "number") { try { closeSync(outFd); } catch {} }
        bridgeChild = null;
        ctx.logger?.info?.(`[dsh-wxauto] 双向桥进程退出（code=${code}）`);
        if (bridgeWanted) {
          // 进程退出但开关仍开：大概率微信未打开/被关闭。每 60s 自动重试，微信打开后自动恢复。
          logBridge(`⚠️ 双向桥进程退出（code=${code}）。若因微信客户端未打开，将每 60 秒自动重试；打开微信后即可恢复。`);
          bridgeRetryTimer = setTimeout(() => {
            bridgeRetryTimer = null;
            if (bridgeWanted && !bridgeChild && bridgeState !== "starting") startBridge(resolved);
          }, 60000);
        } else {
          bridgeState = "stopped";
        }
      });
      ctx.logger?.info?.(
        `[dsh-wxauto] 双向桥已启动（${py.join(" ")} ${BRIDGE_SCRIPT}${chats ? ` --who ${chats}` : ""}）`
      );
    } catch (error) {
      if (typeof outFd === "number") { try { closeSync(outFd); } catch {} }
      bridgeChild = null;
      bridgeState = "failed";
      logBridge(`❌ 双向桥启动失败：${error?.message ?? error}（找不到 Python？可在环境变量 DSH_WX_PYTHON 指定解释器路径）`);
      ctx.logger?.warn?.(`[dsh-wxauto] 启动双向桥失败：${error?.message ?? error}`);
    }
  }

  function syncBridge() {
    if (bridgeWanted) {
      if (!bridgeChild && bridgeState !== "starting") startBridge(resolved);
    } else {
      killAllWxauto("总开关关闭"); // 灭杀所有相关进程（含手动/残留）
    }
  }

  // ── 监听自动启动（与桥同构：listenEnabled=on 自动拉起 wx_listen.py，off 杀整棵树） ──
  function stopListen(reason) {
    if (listenRetryTimer) { clearTimeout(listenRetryTimer); listenRetryTimer = null; }
    if (listenChild) {
      const pid = listenChild.pid;
      try {
        if (process.platform === "win32") {
          spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
        } else {
          listenChild.kill();
        }
      } catch (error) {
        ctx.logger?.warn?.(`[dsh-wxauto] 停止监听进程树失败（pid=${pid}）：${error?.message ?? error}`);
      }
      listenChild = null;
      listenState = "stopped";
      ctx.logger?.info?.("[dsh-wxauto] 监听已停止" + (reason ? `（${reason}）` : ""));
    } else if (listenState !== "stopped") {
      listenState = "stopped";
    }
  }

  function startListen(cfg) {
    if (listenRetryTimer) { clearTimeout(listenRetryTimer); listenRetryTimer = null; }
    if (listenChild || listenState === "starting") return;
    const chats = (cfg.listenChats ?? []).join(",");
    listenState = "starting";
    logBridge("⚠️ 监听自动启动：wx_listen.py 轮询控制聊天；关闭 listenEnabled 即停止。");
    const py = resolvePython();
    let outFd = "ignore";
    try { outFd = openSync(BRIDGE_LOG, "a"); } catch { /* 打不开日志就丢弃输出 */ }
    try {
      listenChild = spawn(py[0], [...py.slice(1), LISTEN_SCRIPT, ...(chats ? ["--who", chats] : [])], {
        cwd: SKILL_DIR,
        stdio: ["ignore", outFd, outFd],
        windowsHide: true,
      });
      listenState = "running";
      listenChild.on("exit", (code) => {
        if (typeof outFd === "number") { try { closeSync(outFd); } catch {} }
        listenChild = null;
        ctx.logger?.info?.(`[dsh-wxauto] 监听进程退出（code=${code}）`);
        if (listenWanted) {
          logBridge(`⚠️ 监听进程退出（code=${code}）。每 60 秒自动重试。`);
          listenRetryTimer = setTimeout(() => {
            listenRetryTimer = null;
            if (listenWanted && !listenChild && listenState !== "starting") startListen(resolved);
          }, 60000);
        } else {
          listenState = "stopped";
        }
      });
      ctx.logger?.info?.(
        `[dsh-wxauto] 监听已启动（${py.join(" ")} ${LISTEN_SCRIPT}${chats ? ` --who ${chats}` : ""}）`
      );
    } catch (error) {
      if (typeof outFd === "number") { try { closeSync(outFd); } catch {} }
      listenChild = null;
      listenState = "failed";
      logBridge(`❌ 监听启动失败：${error?.message ?? error}`);
      ctx.logger?.warn?.(`[dsh-wxauto] 启动监听失败：${error?.message ?? error}`);
    }
  }

  function syncListen() {
    if (listenWanted) {
      if (!listenChild && listenState !== "starting") startListen(resolved);
    } else {
      stopListen("开关关闭");
    }
  }

  function applySettings(cfg) {
    resolved = cfg;
    writeSettingsJson(cfg);
    // 单一总开关 enabled（兼容旧字段 bridgeEnabled/listenEnabled）
    const on = Boolean(cfg.enabled ?? (cfg.bridgeEnabled || cfg.listenEnabled));
    bridgeWanted = on;
    bridgeMode = "full"; // 一体化守护：桥+监听都在一个进程里
    // 去抖：合并启动瞬间的多次 onChange，只同步一次
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = setTimeout(() => { syncTimer = null; syncBridge(); }, 150);
  }

  let sourceThunk = () => resolved;
  installSettingsSection(ctx, "wxauto", WxautoSchema, entry, {
    setSource: (fn) => { sourceThunk = fn; },
    onChange: () => {
      try {
        const value = sourceThunk?.();
        if (value && typeof value === "object") applySettings(value);
      } catch (error) {
        ctx.logger?.warn?.(`[dsh-wxauto] 应用设置失败：${error?.message ?? error}`);
      }
    },
  });

  // 记录 settings 服务（供 setBridgeEnabled 编程开关）
  ctx.inject?.(["settings"], (sctx) => { settingsService = sctx.settings; });

  // ── 3) ctx.wxauto 服务 ────────────────────────────────────────────
  ctx.provide("wxauto", {
    name: "wxauto-bridge",
    skillName: SKILL_NAME,
    skillDir: SKILL_DIR,
    configFile: join(SKILL_DIR, "config.json"),
    settingsFile: SETTINGS_JSON,
    dshBase: () => resolved?.dshBase ?? entry.dshBase ?? "http://127.0.0.1:3080",
    get bridgeEnabled() { return Boolean(resolved?.bridgeEnabled); },
    get listenEnabled() { return Boolean(resolved?.listenEnabled); },
    get bridgeState() { return bridgeState; },
    get listenState() { return bridgeChild ? bridgeState : "stopped"; },
    get config() { return resolved; },
    get enabled() { return bridgeWanted; },
    describe() {
      const on = Boolean(resolved?.enabled ?? (resolved?.bridgeEnabled || resolved?.listenEnabled));
      return {
        skillName: SKILL_NAME,
        skillDir: SKILL_DIR,
        configFile: join(SKILL_DIR, "config.json"),
        settingsFile: SETTINGS_JSON,
        mode: bridgeWanted ? "full" : "off",
        enabled: on,
        state: bridgeState,
        dshBase: resolved?.dshBase ?? entry.dshBase ?? "http://127.0.0.1:3080",
        bridgeChats: resolved?.bridgeChats ?? [],
        targetChats: resolved?.targetChats ?? [],
        listenChats: resolved?.listenChats ?? [],
      };
    },
    /** 编程式总开关：写 settings（wxauto.enabled），由 onChange 统一生效。 */
    async setEnabled(value) {
      if (!settingsService) throw new Error("settings 服务不可用");
      await settingsService.update(settingsNamespace("wxauto"), { enabled: Boolean(value) });
    },
  });

  ctx.logger?.info?.(
    `[dsh-wxauto] 已注册技能 ${SKILL_NAME}（目录 ${SKILL_DIR}）；` +
    `设置命名空间 wxauto（总开关=${bridgeWanted}，状态=${bridgeState}）`
  );

  // 卸载时灭杀所有相关进程并清理定时器
  return {
    dispose: () => {
      if (syncTimer) { clearTimeout(syncTimer); syncTimer = null; }
      killAllWxauto("插件卸载");
    },
  };
}
