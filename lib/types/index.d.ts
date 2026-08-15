/** @dsh-user/dsh-wxauto 服务与插件的类型定义。 */
export interface WxautoConfig {
  /** DSH web 地址（默认 DSH_WEB_URL 或 http://127.0.0.1:3080）。 */
  dshBase?: string;
  /** 新会话工作目录。 */
  cwd?: string;
  /** 任务进度发送目标（@config 时使用）。 */
  targetChats?: string[];
  /** 监听聊天。 */
  listenChats?: string[];
  /** 桥的控制聊天。 */
  bridgeChats?: string[];
  /** 轮询间隔（秒）。 */
  listenInterval?: number;
  /** 任务等待超时（秒）。 */
  taskTimeout?: number;
  /** 任务完成自动汇报。 */
  autoReport?: boolean;
  reportPrefix?: string;
  reportTail?: string;
  /** 双向桥开关：开启=拉起 wx_bridge.py（占用微信窗口）。 */
  bridgeEnabled?: boolean;
  /** 监听开关：关闭时 wx-listen 拒绝启动。 */
  listenEnabled?: boolean;
}

/** ctx.wxauto 服务。 */
export interface WxautoService {
  readonly name: "wxauto-bridge";
  readonly skillName: string;
  readonly skillDir: string;
  readonly configFile: string;
  readonly settingsFile: string;
  readonly bridgeEnabled: boolean;
  readonly listenEnabled: boolean;
  readonly bridgeState: "stopped" | "starting" | "running" | "failed";
  readonly config: WxautoConfig | null;
  dshBase(): string;
  describe(): {
    skillName: string;
    skillDir: string;
    configFile: string;
    settingsFile: string;
    bridge: { enabled: boolean; state: string };
    listen: { enabled: boolean };
    dshBase: string;
    bridgeChats: string[];
    targetChats: string[];
    listenChats: string[];
  };
  /** 编程式开关：写 settings（wxauto.bridgeEnabled），由 onChange 统一生效。 */
  setBridgeEnabled(value: boolean): Promise<void>;
}

declare module "@deepseek-ai/cordis" {
  interface Context {
    wxauto: WxautoService;
  }
}
