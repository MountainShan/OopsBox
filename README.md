# >_ OopsBox v2

[English](#english) | [繁體中文](#繁體中文)

<p align="center">
  <em>I just wanted to code on iPad. Somehow I ended up building a platform. Then I deleted half of it and called it v2. Then I kept adding things back. I'm fine.</em><br><br>
  <em>我只是想在 iPad 上寫 code。結果做了個平台。然後把一半功能砍掉說這叫 v2。然後又慢慢加回來。我沒事的。</em>
</p>

---

## English

I just wanted to code on iPad.

That turned into a web-based dev platform with AI chat, Telegram bots, a code editor, encrypted token storage, and s6-overlay managing 14 processes. It was impressive. It was held together with shell scripts and hope. So I rewrote it, deleted everything I'd been "meaning to clean up," and called it v2.

Then I kept adding things. Because apparently I cannot stop.

This is OopsBox v2. It's a browser-based control panel for running AI coding agents on a remote server — from iPad, phone, or laptop, without a real keyboard, without VPN, without that specific kind of suffering that comes from trying to press Ctrl+C on a touch screen. You get a full terminal, a file manager with an actual editor, and Claude Code running autonomously while you sip coffee and pretend you're supervising.

### screenshots

<p align="center">
  <img src="images/login.png" alt="Login screen" width="80%"><br>
  <em>"somehow alive" — the most accurate project description I've ever written</em>
</p>

<p align="center">
  <img src="images/terminal.png" alt="SSH remote project with Claude running" width="80%"><br>
  <em>An SSH remote project. Claude runs inside the container but every command it runs executes on the remote server. The claude / remote / local buttons in the top-right let you switch tmux windows from the browser — no keyboard shortcuts required, no prefix gymnastics. The status bar at the bottom is real. Claude is genuinely working.</em>
</p>

<p align="center">
  <img src="images/agent.png" alt="Claude making edits in the terminal" width="80%"><br>
  <em>Claude doing a file diff. You didn't fully read what it was about to change. You clicked approve anyway. Welcome to the future of software development.</em>
</p>

### what even is this

A Docker container that gives you a web dashboard for managing AI coding agents on a remote server. The core loop: open a browser on iPad → get a terminal → run Claude Code → Claude does things → you watch (and occasionally supervise).

The non-obvious part is how it's wired together. Each project gets its own isolated tmux session. Claude Code runs inside tmux, with `claude-loop.sh` restarting it automatically if it exits. ttyd streams that tmux session to your browser as a real terminal. nginx sits in front of everything and requires a login for every route. The whole thing starts with one `docker run`.

For SSH remote projects, it goes further: Claude runs locally inside the container, but a shell wrapper transparently forwards every bash command it runs to the remote server over SSH. Claude thinks it's on the remote machine. For most purposes, it is. You also get a dual-panel file manager — local files on the left, remote SFTP files on the right, with one-click transfer between them.

### features

**Web terminal**  
ttyd + tmux, one isolated session per project. The toolbar has ^C, ^D, ^Z, ^L, Tab, and arrow keys — because Apple decided iPad users don't need a real keyboard and we've decided to compensate. Window tab buttons (claude / shell for local projects; claude / remote / local for SSH) let you switch between tmux windows directly from the browser. The active tab stays in sync with the actual tmux window state — switch with keyboard shortcuts inside the terminal and the UI follows. tmux mouse mode is off, so browser text selection and copy-paste work like a normal webpage.

**File manager**  
Browse, upload, download, rename, delete, create files and folders. Breadcrumb navigation. File type icons. For SSH projects: dual-panel view with local on the left and remote SFTP on the right, plus → / ← transfer buttons to move files between sides. Refresh button because sometimes things change and you want to know about it.

**File viewer and editor**  
Click any file and it opens in a viewer. Code and text files open in Monaco Editor — yes, the VS Code one, because we came crawling back. Markdown renders as HTML with a Source/Preview toggle. Images display inline. PDFs open in the browser. Ctrl+S saves. The folder view refreshes automatically after saving so you always see the current state.

**Project management**  
Create local projects (git init, CLAUDE.md, done) or SSH remote projects (host, user, remote path, and either a password or an SSH key path — we handle the rest). Credentials are stored in the project registry and used for both the terminal connection and SFTP file access. Start a project to launch its tmux session and ttyd terminal. Stop it to clean up. Delete it to remove the registry entry. Your files survive deletion — we're not monsters.

**PWA**  
Add it to your home screen. Works as a proper installed app on iOS and Android. The UI shell loads offline. The terminal still needs the server — we're managing expectations, not performing miracles.

**Auth**  
PBKDF2-SHA256, 600,000 iterations, session cookies, nginx `auth_request` on every protected route. Minimum viable security done properly. Auto-generates a password on first boot if you don't set one, prints it once to docker logs, stores it hashed forever.

**System stats**  
CPU, RAM, disk in the nav bar. Updates every 5 seconds. Exists so you can watch your server suffer in real time while your agent rewrites things you haven't reviewed yet.

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

Open `http://localhost:8080`. Log in. Create a project. Start it. You now have a platform. Oops.

If you skip `OOPSBOX_PASSWORD`, it auto-generates one and prints it to `docker logs oopsbox`. Once. Go look now.

| Environment Variable | Default | Description |
|---|---|---|
| `OOPSBOX_PASSWORD` | auto-generated | Dashboard login password |
| `OOPSBOX_USERNAME` | `admin` | Dashboard login username |
| `GIT_NAME` | — | Git author name |
| `GIT_EMAIL` | — | Git author email |
| `SSL_CERT` | — | Path to SSL cert (inside container) |
| `SSL_KEY` | — | Path to SSL key (inside container) |

| Volume | Container Path | Contents |
|---|---|---|
| `oopsbox-projects` | `/oopsbox/projects` | Project files and registry |
| `oopsbox-config` | `/oopsbox/.config/oopsbox` | Auth credentials, encryption keys |
| `oopsbox-claude` | `/oopsbox/.claude` | Claude CLI sessions and settings |

Or mount a `config.yaml` at `/oopsbox/config.yaml`:

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

### how it works

```
browser → nginx (auth_request on every protected route)
        → FastAPI / uvicorn (auth, projects, files, ssh, system)
        → ttyd per project (proxied at /terminal/<project>/)

per-project isolated tmux sessions:
  oopsbox-myproject               ← local project
  ├── claude  (claude-loop.sh, auto-restarts on exit)
  └── shell   (plain bash)

  oopsbox-remote-project          ← SSH remote project
  ├── claude  (runs locally; SHELL=remote-bash forwards all commands via SSH)
  ├── remote  (interactive SSH session on the remote server)
  └── local   (plain bash for local config)

supervisord manages:
  - nginx
  - uvicorn (port 5000)
  that's it. two processes.
```

For SSH projects, Claude's bash tool is intercepted by a generated wrapper script (`remote-bash`) that forwards every command to the remote server over SSH. Claude's file edits, git operations, and test runs all happen there. The file manager uses paramiko SFTP to browse and transfer files on both sides independently.

### project types

**Local** — code lives on the server inside the container. Start the project and you get a tmux session with Claude running in one window and a plain shell in another. Claude Code, your codebase, a terminal. Standard.

**SSH remote** — point it at any server with SSH access. Claude runs in the container but all its actions execute on the remote machine. Useful when your actual code lives on a Proxmox VM, VPS, or homelab server. The dual-panel file manager shows local and remote files side by side and lets you transfer between them with one click.

### tested on

| Environment | Status |
|---|---|
| Docker on Ubuntu 24.04 | ✅ |
| Docker on Debian 12 | 🤷 probably |
| Proxmox VM | ✅ |
| That machine with the weird routing tables | ✅ (one `ip route` and a brief identity crisis) |

| Device | Status |
|---|---|
| iPad Safari | ✅ (the whole point) |
| iPhone Safari | ✅ mostly |
| Chrome | ✅ |
| Firefox | ✅ |

### FAQ

**Q: Where did the AI chat go?**  
A: v1. It had auto-restart, `--resume` session persistence, and interactive prompt buttons. It was good. It's gone. We don't talk about it.

**Q: Where did Telegram go?**  
A: Same place. Moving on.

**Q: Where did the code editor go?**  
A: We said it was gone. Then we added Monaco Editor back via the file viewer. We have no self-control. Click any file — it's there.

**Q: Is this production ready?**  
A: This question implies I had a plan. The plan was "code on iPad." Everything else was iterative.

**Q: Should I use this?**  
A: I use it daily. Make of that what you will.

**Q: Will you add back the features you removed?**  
A: I said no. I added the file editor back. I added SSH file transfer back. I added the Monaco editor back. Please stop asking.

**Q: What's next?**  
A: I don't know. Last time I answered this I ended up with encrypted Telegram bots. I'm taking a moment.

### license

MIT. Do whatever. If it breaks, that's between you and Docker. I just wanted to type `ls` on an iPad without suffering, and somehow I'm still here two major versions later, adding things at midnight. Send help. Or a star. Either works.

---

## 繁體中文

我只是想在 iPad 上寫 code。

結果做了個有 AI 對話、Telegram bot、code editor、加密 token 儲存、s6-overlay 管 14 個 process 的平台。很壯觀。也是用 shell script 和信念撐著的。所以我重寫了，把「遲早要清」的全刪了，叫它 v2。

然後又慢慢加回來。因為我就是停不下來。

這是 OopsBox v2。一個在瀏覽器裡控制 AI coding agent 的平台 — 從 iPad、手機、或電腦，不需要實體鍵盤，不需要 VPN，也不需要忍受在觸控螢幕上按 Ctrl+C 的那種折磨。你有完整的 terminal、有編輯器的檔案管理器，還有 Claude Code 在背景自主作業，而你喝著咖啡假裝自己在「監督」。

### 截圖

<p align="center">
  <img src="images/login.png" alt="登入畫面" width="80%"><br>
  <em>"somehow alive" — 我寫過最精準的專案描述</em>
</p>

<p align="center">
  <img src="images/terminal.png" alt="SSH 遠端專案，Claude 正在執行" width="80%"><br>
  <em>SSH 遠端專案。Claude 在 container 裡跑，但它執行的每個指令都透過 SSH 轉發到遠端 server。右上角的 claude / remote / local 按鈕讓你直接從瀏覽器切換 tmux 視窗，不需要記任何快捷鍵。下面那個狀態列是真的，Claude 在工作。</em>
</p>

<p align="center">
  <img src="images/agent.png" alt="Claude 正在做檔案修改" width="80%"><br>
  <em>Claude 在做 diff。你沒有完全看清楚它要改什麼。你還是按了 approve。歡迎來到軟體開發的未來。</em>
</p>

### 這到底是什麼

一個 Docker container，裡面跑著一個 web dashboard，讓你管理遠端 server 上的 AI coding agent。核心流程：在 iPad 上打開瀏覽器 → 有 terminal → 跑 Claude Code → Claude 做事 → 你看著（偶爾監督一下）。

不那麼直觀的部分是底層的架構。每個專案有自己獨立的 tmux session，Claude Code 在 tmux 裡跑，`claude-loop.sh` 會在它退出時自動重啟。ttyd 把 tmux session 串流到瀏覽器，變成一個真正的 terminal。nginx 在最前面，每個路由都需要登入。整個東西一個 `docker run` 就起來了。

SSH 遠端專案走得更遠：Claude 在 container 本地跑，但有一個 shell wrapper 會把它執行的每個 bash 指令透明地透過 SSH 轉發到遠端執行。Claude 以為自己在遠端機器上。就大多數情況來說也確實如此。你還會得到一個雙面板檔案管理器 — 左邊本地、右邊遠端 SFTP，一鍵互傳。

### 功能

**Web Terminal**  
ttyd + tmux，每個專案獨立 session。Toolbar 有 ^C、^D、^Z、^L、Tab、方向鍵 — 因為 Apple 決定 iPad 使用者不需要真正的鍵盤，而我們決定補上。視窗切換按鈕（本機專案：claude / shell；SSH 專案：claude / remote / local）讓你直接在瀏覽器切換 tmux 視窗，不用記 prefix，不用背快捷鍵。按鈕 active 狀態與 tmux 實際視窗同步，在 terminal 裡用鍵盤切換視窗，UI 也會跟著更新。tmux mouse mode 已關閉，瀏覽器的文字選取和複製貼上可以正常使用。

**檔案管理器**  
瀏覽、上傳、下載、重命名、刪除、建立檔案和資料夾。麵包屑導航。副檔名圖示。SSH 專案有雙面板：左邊本地、右邊遠端 SFTP，→ / ← 按鈕一鍵互傳。重新整理按鈕讓你隨時確認資料夾的最新狀態。

**檔案檢視器與編輯器**  
點一下檔案就開啟。程式碼和文字檔用 Monaco Editor 開——對，就是 VS Code 那個，因為我們爬回來了。Markdown 渲染成 HTML，有 Source / Preview 切換。圖片直接顯示，PDF 在瀏覽器裡開。Ctrl+S 儲存，儲存後自動刷新資料夾。

**專案管理**  
建立本機專案（git init、CLAUDE.md、完成）或 SSH 遠端專案（填入 host、帳號、遠端路徑，以及密碼或 SSH 金鑰路徑擇一，其他我們來）。憑證儲存在 project registry，terminal 連線和 SFTP 檔案存取都用同一份。啟動就開 tmux session 和 ttyd terminal，停止就清理，刪除就移除 registry 登記。你的檔案在刪除後還在——我們不是惡人。

**PWA**  
加到主畫面，像個正常 app 一樣開。iOS 和 Android 都支援。UI shell 可離線載入。Terminal 還是需要 server 連線——我們在管理預期，不是在施魔法。

**認證**  
PBKDF2-SHA256 600,000 次迭代、session cookie、nginx `auth_request` 在每個受保護路由上。最低限度的安全層，做得認真。沒設密碼的話第一次啟動自動產生，只印一次到 docker logs，之後永遠以雜湊儲存。

**系統監控**  
CPU、RAM、磁碟在 nav bar，每 5 秒更新。讓你即時看到 Claude 在動你的 codebase 時 server 有多痛苦。

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

打開 `http://localhost:8080`。登入。建立專案。啟動。你現在有一個平台了。Oops。

沒設 `OOPSBOX_PASSWORD` 的話，它自動產生一個印在 `docker logs oopsbox` 裡。只印一次。快去看。

| 環境變數 | 預設值 | 說明 |
|---|---|---|
| `OOPSBOX_PASSWORD` | 自動產生 | Dashboard 登入密碼 |
| `OOPSBOX_USERNAME` | `admin` | Dashboard 登入帳號 |
| `GIT_NAME` | — | Git 作者名稱 |
| `GIT_EMAIL` | — | Git 作者信箱 |
| `SSL_CERT` | — | SSL 憑證路徑（container 內） |
| `SSL_KEY` | — | SSL 金鑰路徑（container 內） |

| Volume | 容器路徑 | 內容 |
|---|---|---|
| `oopsbox-projects` | `/oopsbox/projects` | 專案檔案和 registry |
| `oopsbox-config` | `/oopsbox/.config/oopsbox` | 認證資訊、加密金鑰 |
| `oopsbox-claude` | `/oopsbox/.claude` | Claude CLI session 和設定 |

或者掛 `config.yaml` 到 `/oopsbox/config.yaml`：

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

### 大概怎麼運作的

```
瀏覽器 → nginx（每個受保護路由都走 auth_request）
        → FastAPI / uvicorn（auth、projects、files、ssh、system）
        → 每個專案一個 ttyd（代理在 /terminal/<project>/）

每個專案獨立的 tmux session：
  oopsbox-myproject               ← 本機專案
  ├── claude  （claude-loop.sh，退出自動重啟）
  └── shell   （純 bash）

  oopsbox-remote-project          ← SSH 遠端專案
  ├── claude  （本地跑；SHELL=remote-bash 把所有指令透過 SSH 轉發）
  ├── remote  （遠端 server 的互動式 SSH session）
  └── local   （本地 bash，用來管設定）

supervisord 管的：
  - nginx
  - uvicorn（port 5000）
  就這兩個。
```

SSH 專案中，Claude 的 bash tool 被一個生成的 wrapper script（`remote-bash`）攔截，把每個指令透過 SSH 轉發到遠端執行。Claude 的檔案編輯、git 操作、測試都在那邊跑。檔案管理器用 paramiko SFTP 獨立瀏覽兩側的檔案，並可互傳。

### 專案類型

**本機** — code 住在 container 裡的 server 上。啟動就得到一個 tmux session，claude 在一個視窗、純 shell 在另一個。Claude Code、你的 codebase、terminal。標準配置。

**SSH 遠端** — 指向任何有 SSH 存取的 server。Claude 在 container 裡跑，但所有動作在遠端執行。適合 codebase 住在 Proxmox VM、VPS 或 homelab server 上的情況。雙面板檔案管理器並排顯示本地和遠端，一鍵互傳。

### 測試過的平台

| 環境 | 狀態 |
|---|---|
| Docker on Ubuntu 24.04 | ✅ |
| Docker on Debian 12 | 🤷 大概行 |
| Proxmox VM | ✅ |
| 那台路由表很奇怪的機器 | ✅（一個 `ip route` 加上短暫的身份危機） |

| 裝置 | 狀態 |
|---|---|
| iPad Safari | ✅（重點就是這個） |
| iPhone Safari | ✅ 大致能用 |
| Chrome | ✅ |
| Firefox | ✅ |

### 常見問題

**問：AI 對話去哪了？**  
答：v1。有自動重啟、`--resume` 持久化、互動提示按鈕。很好用。現在不見了。我們不談這個。

**問：Telegram 呢？**  
答：一起走了。繼續往前看。

**問：Code editor 去哪了？**  
答：我們說不見了。然後透過檔案檢視器把 Monaco Editor 加回來了。我們沒有自制力。點任何檔案，它在那裡。

**問：這能上 production 嗎？**  
答：這個問題暗示我有計劃。計劃是「iPad 寫 code」。其他都是迭代。

**問：我該用嗎？**  
答：我每天在用。這算推薦還是警告，取決於你的風險承受度。

**問：你會把砍掉的功能加回來嗎？**  
答：我說不會。然後把檔案編輯器加回來了。把 SSH 檔案傳輸加回來了。把 Monaco 加回來了。請不要再問了。

**問：下一步是什麼？**  
答：我不知道。上次我回答這個問題，結果冒出了加密 Telegram bot。我先暫停一下。

### 授權

MIT。愛怎麼用就怎麼用。壞了不關我的事。我只是想在 iPad 上打 `ls` 不想哭，然後兩個大版本之後的現在我還在這裡半夜加功能。救救我。或者給個 star 也行。都行。
