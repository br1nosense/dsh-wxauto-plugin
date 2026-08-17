# dsh-wxauto-plugin — DSH WeChat reporting & listening plugin

A DSH skill plugin built on **wxauto4** (free tier, supports WeChat 4.1.x).

**Core features**
1. **Task progress push**: when a DSH task starts or finishes, send progress
   messages (text or files) to the WeChat chat you configure.
2. **Message listening**: watch selected chats, append new messages to a JSONL
   log for the agent to read, and reply automatically to configured keywords.
3. **WeChat ⇄ DSH two-way bridge**: from WeChat, send commands or plain
   messages to switch/new conversations, drive DSH tasks, and receive progress
   **screenshots** (session state rendered to PNG) plus completion reports.

## Install (from GitHub)

```powershell
# 1. Clone the repository
git clone https://github.com/br1nosense/dsh-wxauto-plugin.git
cd dsh-wxauto-plugin

# 2. Install Python dependencies (wxauto4 free tier + websocket-client:
#    the bridge needs it to forward DSH questions to WeChat)
py -3 -m pip install wxauto4 websocket-client

# 3. Install as a DSH plugin (registers into the default web profile)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 4. Restart `dsh web` for the plugin to take effect
```

> - Requirements: Windows 10/11 + WeChat 4.1.x client (signed in) + Python 3.9–3.12.
> - Uninstall: `dsh plugin --profile web rm @dsh-user/dsh-wxauto` then restart `dsh web`.

## How the bundle works

This package is a standard DSH profile bundle:

- `package.json` declares `dsh.bundle.patch: ./cordis.patch.yml`, so
  `dsh plugin add` registers it into the profile's bundle layer.
- `cordis.patch.yml` mounts the `wxauto` plugin line (injecting the `skills`
  and `settings` services).
- `lib/index.js` registers the `dsh-wxauto` skill into `ctx.skills`
  (`resourceBase` points at this package's `skills/dsh-wxauto` directory),
  registers a `wxauto` settings namespace (persisted under `wxauto:` in
  `~/.dsh/settings.yaml`), and provides the `ctx.wxauto` service (bridge
  on/off, bridge state, config mirror `data/dsh_settings.json`).
- `lib/client.js` registers a dedicated **WeChat Automation** tab in the
  settings sidebar.

**Bridge switch**: `wxauto.enabled` (settings page tab or settings.yaml).
On = auto-start the bridge (it **occupies the local WeChat window**; do not
operate WeChat manually meanwhile). Off = auto-stop.

## Two-way bridge commands

Send these to the control chat:

```
/help /new /list /switch <index|prefix> /active /status /shot /history [n] /task <content> /cancel
A plain message = hand that content to the current DSH session as a task
```

DSH's `ask_user_question` prompts are forwarded to WeChat with numbered
options; replying with an option number (or `index: answer`, `0` to skip,
comma-separated for multiple) feeds the answer back and the task continues.

## Command reference

| Command | Purpose |
|---|---|
| `wx-test.ps1` | Environment preflight (Python / wxauto4 / WeChat process / login) |
| `wx-sessions.ps1` | List sessions (confirm chat names) |
| `wx-send.ps1` | Send progress/completion info (text/file, multiple targets, `@config`) |
| `wx-read.ps1` | Read the last N messages of a chat |
| `wx-listen.ps1` | Listen (polling, JSONL to disk + keyword auto-reply; `-Once` for one pass) |
| `wx-bridge.ps1` | WeChat ⇄ DSH two-way bridge |
| `dsh-list.ps1` / `dsh-new.ps1` / `dsh-switch.ps1` | List / new / switch sessions |
| `dsh-status.ps1` / `dsh-shot.ps1` / `dsh-history.ps1` | Progress text / progress screenshot / recent conversation |
| `dsh-task.ps1` / `dsh-cancel.ps1` | Run a task / cancel the current turn |

## Compliance note

wxauto's official stance is that the code is for UIAutomation technical
learning only — not for production, marketing, or illegal use. Frequent bulk
messaging and mass friending risk account bans. Confirm recipients and
frequency before sending to anyone other than yourself.

## License

MIT — see [LICENSE](./LICENSE).
