# >_ OopsBox v2

[English](#english) | [繁體中文](#繁體中文)

<p align="center">
  <em>I just wanted to code on iPad. Somehow I ended up building a platform. Then I deleted half of it and called it v2.</em><br>
  <em>我只是想在 iPad 上寫 code。結果做了個平台。然後把一半功能砍掉，說這叫 v2。</em>
</p>

<p align="center">
  <strong>⚠️ This is v2. We removed the AI chat. We removed Telegram. We removed the code editor. We removed s6-overlay. We removed the install script. We called it "simplification." We're choosing to be proud of this.</strong><br><br>
  <strong>⚠️ 這是 v2。我們砍掉了 AI 對話。砍掉了 Telegram。砍掉了 code editor。砍掉了 s6-overlay。砍掉了安裝腳本。我們稱之為「簡化」。我們選擇為此感到驕傲。</strong>
</p>

---

## English

I just wanted to code on iPad.

Somehow that turned into a web-based dev platform with AI agent chat, Telegram bots, a code editor, encrypted token storage, and s6-overlay managing 14 processes. It was impressive. It was also held together with shell scripts and hope.

So I rewrote it. I deleted everything I'd been "meaning to clean up." I removed features I was "definitely going to use." I replaced s6-overlay with supervisord because the only thing scarier than managing your own process supervisor is pretending you know how s6 works.

This is OopsBox v2. It does less. It works better. I'm coping.

### what even is this

A Docker-based web dashboard for running coding agents (Claude Code, Codex, etc.) on a remote server and controlling everything from a browser — iPad, phone, laptop, whatever. You get a terminal with actual control key buttons (because try pressing Ctrl+C on an iPad, I dare you), a file manager, project management for local and SSH remotes, and system stats because watching CPU go brrr while your agent rewrites your codebase is the modern campfire experience.

The AI chat is gone. The Telegram bot is gone. The code editor is gone. What remains is what I actually use every day and maintain without crying. Progress.

### what's left (the feature list that survived the purge)

- **Web terminal** — ttyd + tmux, one per project. Toolbar has ^C, ^D, ^Z, ^L, Tab, arrow keys, and an expandable extra-keys panel — because Apple decided iPad users don't need a real keyboard and I've decided to spite them. Per-project sessions backed by tmux windows. Starts when you start the project. Stops when you stop it. Does what it says.
- **File manager** — browse directories, upload files, download files, rename things, delete things, create folders. Double-click to descend. "Up" button to go up. Breadcrumb navigation. File type icons because life is too short for 📄 everything. No drag-and-drop, no context menus, no right-click — we are in a season of restraint.
- **Project management** — create local projects (git init, CLAUDE.md, registry entry — done) or SSH remote projects (runs here, executes there via paramiko, works better than it should, makes me nervous). Start to launch ttyd + tmux. Stop to clean up. Delete to nuke the registry (files stay — not our problem).
- **System stats** — CPU, RAM, disk in the nav bar. Refreshes every 5 seconds. Exists so you can watch your server suffer in real time while Claude rewrites things.
- **Login auth** — PBKDF2-SHA256 with 600,000 iterations, session cookies, nginx `auth_request`. The minimum viable security layer. Random password generated on first boot if you don't set one, printed to docker logs, gone from logs on restart, stored hashed forever. Very normal.
- **Docker-only** — no install script, no systemd, no bare metal option. One container. supervisord manages nginx + uvicorn. Mount your volumes, pass your env vars, pretend you planned this.

### quick start

```bash
docker run -d \
  --name oopsbox \
  -p 8080:80 \
  -e OOPSBOX_PASSWORD=yourpassword \
  -e GIT_NAME="Your Name" \
  -e GIT_EMAIL="you@example.com" \
  -v oopsbox-projects:/oopsbox/projects \
  -v oopsbox-config:/oopsbox/.config/oopsbox \
  -v oopsbox-claude:/oopsbox/.claude \
  oopsbox
```

Open `http://localhost:8080`. Log in. You now have a platform. Oops.

If you skip `OOPSBOX_PASSWORD`, it auto-generates one and prints it to `docker logs oopsbox`. Once. Check immediately. It's not printed again. I'm not sure if this is a security feature or a "read the docs" tax. Probably both.

| Environment Variable | Required | Description |
|---|---|---|
| `OOPSBOX_PASSWORD` | No (auto-generated) | Dashboard login password |
| `OOPSBOX_USERNAME` | No (default: `admin`) | Dashboard login username |
| `GIT_NAME` | No | Git author name |
| `GIT_EMAIL` | No | Git author email |
| `SSL_CERT` | No | Path to SSL cert (inside container) |
| `SSL_KEY` | No | Path to SSL key (inside container) |

| Volume | Container Path | What it stores |
|---|---|---|
| `oopsbox-projects` | `/oopsbox/projects` | Your project files and registry |
| `oopsbox-config` | `/oopsbox/.config/oopsbox` | Auth, encryption keys |
| `oopsbox-claude` | `/oopsbox/.claude` | Claude CLI sessions and settings |

Alternatively, mount a `config.yaml` at `/oopsbox/config.yaml`:

```yaml
auth:
  username: admin
  password: yourpassword
git:
  name: Your Name
  email: you@example.com
ssl:
  cert: /path/to/cert.pem
  key: /path/to/key.pem
```

Env vars take priority over config.yaml. Config.yaml takes priority over defaults. Defaults are "admin" and "please set a password."

### how it works

```
browser → nginx → FastAPI (auth_request on protected routes)
               → uvicorn (FastAPI routers: auth, projects, files, system)
               → ttyd per project (proxied at /terminal/<project>/)

all projects share one tmux session ("agents"):
  agents session
  ├── project-name window (ttyd attaches here)
  └── another-project window

supervisord manages:
  - nginx
  - uvicorn (port 5000)
  that's it. two processes. i'm proud of us.
```

nginx handles auth via `auth_request /api/auth/verify` — every request to `/`, `/workspace`, `/terminal/*`, and `/api/*` (except login endpoints) requires a valid session cookie. If you're not logged in, you get redirected to `/login`. This is the security model. It is what it is.

### project types

**Local** — git repo lives on the server. Start it and you get a tmux window + ttyd terminal pointed at it. Claude Code, tmux, your shell — the holy trinity.

**SSH** — your Docker container connects to a remote server via paramiko. Agent runs locally, executes commands over there. Editor will eventually use SFTP (it's on the list). It works surprisingly well and I remain suspicious of it.

### tested on

| Environment | Status |
|---|---|
| Docker on Ubuntu 24.04 | ✅ works |
| Docker on Debian 12 | 🤷 probably |
| Proxmox VM | ✅ works |
| That one machine with the weird routing tables | ✅ works (required one `ip route` command and a brief identity crisis) |

| Device / Browser | Status |
|---|---|
| iPad Safari | ✅ works (the whole point) |
| iPhone Safari | ✅ mostly works |
| Chrome | ✅ works |
| Firefox | ✅ works |

### FAQ

**Q: Where did the AI chat go?**  
A: We removed it. It was beautiful. It had auto-restart loops and interactive prompt buttons and session persistence via `--resume`. It's gone now. We don't talk about it.

**Q: Where did Telegram go?**  
A: Same place. We're not ready to discuss this.

**Q: Where did the code editor go?**  
A: We recommend using Claude Code to edit your files. This is a reasonable position and not at all a justification for removing 800 lines of editor code.

**Q: Is this production ready?**  
A: This question implies I had a plan. The plan was "I wanted to code on iPad." Everything else was an accident.

**Q: Should I use this?**  
A: I use it daily. Whether that's a recommendation or a warning depends on your relationship with risk.

**Q: Why v2 and not just a fix?**  
A: v1 had s6-overlay, 14 supervised processes, a Telegram bot, AES-256-CBC encrypted tokens, a code editor with Mermaid support, and a Skills panel. It was, frankly, a lot. v2 has supervisord and two processes. This is called growth.

**Q: Will you add back the features you removed?**  
A: No. Absolutely not. Don't tempt me. I have a problem.

**Q: What's next?**  
A: I don't know. Last time I answered this question I ended up with encrypted Telegram bots. I'm taking a moment.

### license

MIT — do whatever you want. If it breaks, that's between you and Docker. I just wanted to type `ls` on an iPad without wanting to throw it, and somehow I've built and then rebuilt an entire platform twice. Send help. Or don't. I'll probably be fine. I'll add a feature.

---

## 繁體中文

我只是想在 iPad 上寫 code。

結果做了個有 AI 對話、Telegram bot、code editor、加密 token 儲存、s6-overlay 管 14 個 process 的網頁開發平台。很壯觀。也是用 shell script 和信念撐著的。

所以我重寫了它。把「遲早要清的技術債」全刪了。把「之後一定會用到的功能」都砍了。把 s6-overlay 換成 supervisord，因為比自己管 process supervisor 更可怕的只有假裝你懂 s6 在幹嘛。

這是 OopsBox v2。功能變少了。跑得更穩了。我在自我調適。

### 這到底是什麼

一個 Docker 架設的網頁 dashboard，讓你在遠端 server 上跑 coding agent（Claude Code、Codex 等），然後從瀏覽器控制一切 — iPad、手機、電腦，都行。你會得到一個有實體控制鍵按鈕的 terminal（因為在 iPad 上按 Ctrl+C 是一種折磨，而我選擇反抗），一個檔案管理器，本機和 SSH 遠端的專案管理，還有系統監控，因為看 AI agent 重寫你的 codebase 時 CPU 跑起來是現代版的看營火。

AI 對話不見了。Telegram bot 不見了。Code editor 不見了。剩下的是我每天真的在用、而且維護時不會哭的東西。這叫進步。

### 倖存下來的功能

- **Web terminal** — ttyd + tmux，每個專案一個。Toolbar 有 ^C、^D、^Z、^L、Tab、方向鍵，還有一個可展開的額外按鍵面板 — 因為 Apple 決定 iPad 使用者不需要真正的鍵盤，而我決定跟他們作對。專案各有獨立的 tmux 視窗。啟動專案就有 terminal。停止就清掉。說到做到。
- **檔案管理器** — 瀏覽目錄、上傳、下載、重命名、刪除、建資料夾。雙擊進入。「Up」按鈕返回上層。麵包屑導航。有副檔名圖示，因為人生太短，不想把一切都顯示成 📄。沒有拖放、沒有右鍵選單 — 我們正在過克制的季節。
- **專案管理** — 建立本機專案（git init、CLAUDE.md、registry 登記，完成）或 SSH 遠端專案（在本地跑、透過 paramiko 在遠端執行，效果比它應有的還好，讓我覺得不安）。啟動就開 ttyd + tmux，停止就清理，刪除就從 registry 移除（檔案留著，不關我們的事）。
- **系統監控** — CPU、RAM、磁碟顯示在 nav bar。每 5 秒更新。讓你能即時看到 Claude 在重寫東西時 server 有多痛苦。
- **登入驗證** — PBKDF2-SHA256 600,000 次迭代、session cookie、nginx `auth_request`。最低限度的安全層。第一次啟動若未設密碼，自動產生隨機密碼印到 docker logs，重啟後不會再印，但永遠以雜湊形式儲存。非常正常的流程。
- **Docker 限定** — 沒有安裝腳本，沒有 systemd，沒有裸機選項。一個 container。supervisord 管 nginx + uvicorn。掛 volume、傳 env var、假裝這一切都是計劃好的。

### 快速開始

```bash
docker run -d \
  --name oopsbox \
  -p 8080:80 \
  -e OOPSBOX_PASSWORD=你的密碼 \
  -e GIT_NAME="你的名字" \
  -e GIT_EMAIL="you@example.com" \
  -v oopsbox-projects:/oopsbox/projects \
  -v oopsbox-config:/oopsbox/.config/oopsbox \
  -v oopsbox-claude:/oopsbox/.claude \
  oopsbox
```

打開 `http://localhost:8080`。登入。你現在有一個平台了。Oops。

如果沒設 `OOPSBOX_PASSWORD`，它會自動產生一個然後印在 `docker logs oopsbox` 裡。只印一次。請立刻去看。我不確定這算安全設計還是「自己去讀 log」的懲罰。兩者都是。

| 環境變數 | 必填 | 說明 |
|---|---|---|
| `OOPSBOX_PASSWORD` | 否（自動產生） | Dashboard 登入密碼 |
| `OOPSBOX_USERNAME` | 否（預設：`admin`） | Dashboard 登入帳號 |
| `GIT_NAME` | 否 | Git 作者名稱 |
| `GIT_EMAIL` | 否 | Git 作者信箱 |
| `SSL_CERT` | 否 | SSL 憑證路徑（container 內） |
| `SSL_KEY` | 否 | SSL 金鑰路徑（container 內） |

| Volume | 容器路徑 | 存什麼 |
|---|---|---|
| `oopsbox-projects` | `/oopsbox/projects` | 你的專案檔案和 registry |
| `oopsbox-config` | `/oopsbox/.config/oopsbox` | 認證、加密金鑰 |
| `oopsbox-claude` | `/oopsbox/.claude` | Claude CLI session 和設定 |

或者掛一個 `config.yaml` 到 `/oopsbox/config.yaml`：

```yaml
auth:
  username: admin
  password: 你的密碼
git:
  name: 你的名字
  email: you@example.com
ssl:
  cert: /path/to/cert.pem
  key: /path/to/key.pem
```

環境變數優先於 config.yaml，config.yaml 優先於預設值，預設值是 `admin` 和「拜託去設個密碼」。

### 大概怎麼運作的

```
瀏覽器 → nginx → FastAPI（受保護路由全走 auth_request）
               → uvicorn（FastAPI router：auth、projects、files、system）
               → 每個專案一個 ttyd（代理在 /terminal/<project>/）

所有專案共用一個 tmux session（"agents"）：
  agents session
  ├── project-name 視窗（ttyd 連到這裡）
  └── another-project 視窗

supervisord 管的：
  - nginx
  - uvicorn（port 5000）
  就這兩個。兩個 process。我們為此感到驕傲。
```

nginx 透過 `auth_request /api/auth/verify` 處理認證 — 所有對 `/`、`/workspace`、`/terminal/*`、`/api/*`（除了登入端點）的請求都需要有效的 session cookie。沒登入就被導到 `/login`。這就是安全模型。就這樣。

### 專案類型

**本機** — git repo 住在 server 上。啟動就得到一個 tmux 視窗和指向它的 ttyd terminal。Claude Code、tmux、你的 shell — 神聖三位一體。

**SSH 遠端** — Docker container 透過 paramiko 連到遠端 server。Agent 在本地跑，指令在那邊執行。效果出乎意料地好，這讓我很緊張。

### 測試過的平台

| 環境 | 狀態 |
|---|---|
| Docker on Ubuntu 24.04 | ✅ 能用 |
| Docker on Debian 12 | 🤷 大概行 |
| Proxmox VM | ✅ 能用 |
| 那台路由表很奇怪的機器 | ✅ 能用（需要一個 `ip route` 指令和短暫的身份危機） |

| 裝置 / 瀏覽器 | 狀態 |
|---|---|
| iPad Safari | ✅ 能用（重點就是這個） |
| iPhone Safari | ✅ 大致能用 |
| Chrome | ✅ 能用 |
| Firefox | ✅ 能用 |

### 常見問題

**問：AI 對話去哪了？**  
答：砍掉了。它很美。有自動重啟循環、互動提示按鈕、`--resume` session 持久化。現在不見了。我們不談這個。

**問：Telegram 去哪了？**  
答：同上。我們尚未準備好面對這個話題。

**問：Code editor 去哪了？**  
答：我們建議用 Claude Code 來編輯你的檔案。這是一個合理的立場，完全不是為砍掉 800 行 editor 程式碼找理由。

**問：這能上 production 嗎？**  
答：這個問題暗示我有計劃。計劃是「想在 iPad 上寫 code」。其他都是意外。

**問：我該用這個嗎？**  
答：我每天都在用，這算推薦還是警告取決於你的風險承受度。

**問：為什麼叫 v2 而不是直接修？**  
答：v1 有 s6-overlay、14 個受管 process、Telegram bot、AES-256-CBC 加密 token、Mermaid 的 code editor、和 Skills 面板。說實話，有點太多了。v2 有 supervisord 和兩個 process。這叫成長。

**問：你會把砍掉的功能加回來嗎？**  
答：不會。絕對不會。不要試探我。我有問題。

**問：下一步是什麼？**  
答：我不知道。上次我回答這個問題，結果冒出了加密 Telegram bot。我先暫停一下。

### 授權

MIT — 愛怎麼用就怎麼用。壞了不關我的事。我只是想在 iPad 上打 `ls` 不會想把它摔出去，然後我把一整個平台建了又重建了兩次。救救我。或者不救也行。我大概沒事的。我去加個功能。
