# RVCoder - Remote Vibe Coder

A web-based development environment for managing multiple Claude Code projects with integrated terminal, file editor, and system monitoring.

## Features

- **Project Management** — Create, start, stop, delete projects from web UI
- **Web Terminal** — ttyd-based terminal with tmux, mouse support, dark theme
- **Code Editor** — CodeMirror-based editor with syntax highlighting, Markdown preview with Mermaid support
- **System Terminal** — Full system shell access
- **System Monitor** — Real-time CPU, RAM, Swap, Disk usage
- **Claude Code Integration** — Auto-starts Claude in each project with session resume
- **PWA Support** — Install as app on iPad/desktop
- **Mobile Friendly** — Responsive layout for tablet and desktop

## Architecture

```
nginx (port 80) → FastAPI dashboard (port 5000)
                → ttyd terminals (ports 9100+)
                → system terminal (port 9000)
```

## Quick Install

```bash
git clone <repo-url> RVCoder
cd RVCoder
./install.sh
```

### Prerequisites

- Ubuntu 24.04 LTS
- Node.js 22+ (for Claude Code)
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)
- `ANTHROPIC_API_KEY` set in environment

## File Structure

```
RVCoder/
├── install.sh              # One-click installer
├── bin/                    # Project lifecycle scripts
│   ├── project-create.sh
│   ├── project-start.sh
│   ├── project-stop.sh
│   ├── project-delete.sh
│   ├── project-status.sh
│   ├── get-project-ports.sh
│   ├── nginx-reload-ports.sh
│   ├── claude-loop.sh      # Auto-restart Claude with --continue
│   ├── idle-check.sh       # Stop idle projects (cron)
│   └── system-term.sh      # System terminal manager
├── dashboard/
│   ├── main.py             # FastAPI backend
│   └── static/
│       ├── index.html      # Dashboard SPA
│       ├── editor.html     # Code editor
│       ├── manifest.json   # PWA manifest
│       ├── icon-192.png
│       └── icon-512.png
└── config/
    ├── nginx-site.conf     # nginx reverse proxy config
    ├── dashboard.service   # systemd service
    ├── tmux.conf           # tmux config with mouse + theme
    ├── ttyd-theme.conf     # Terminal dark/light theme colors
    └── statusline-command.sh  # Claude Code status line
```

## Usage

### Dashboard

Open `http://<server-ip>` in your browser.

- Click **New** to create a project
- Click **Enter** to open terminal + editor
- Use **Terminal/Editor** tabs to switch views
- **System Terminal** button in sidebar footer for system access

### Keyboard Shortcuts (Editor)

| Key | Action |
|---|---|
| Ctrl+S | Save |
| Ctrl+Shift+S | Save As |
| Ctrl+N | New File |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| Ctrl+H | Find & Replace |
| Ctrl+F | Find |

### Terminal Controls

- Floating menu on right side: Ctrl+C, Ctrl+D, Pop out
- tmux mouse enabled: click tabs, scroll, resize panes

## License

MIT
