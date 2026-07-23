# glyph

A self-hosted, multi-user, terminal-styled notebook. Plain markdown notes on the left, Apple Pencil drawing on the right — no accounts with a company behind them, no analytics, no dependencies.

Live at **[glyph.dakshhq.com](https://glyph.dakshhq.com)** — open signup, anyone can create an account and gets their own private vault.

**Stack:** Python 3 stdlib only. No dependencies, no build step, no npm, no framework. Frontend is a single HTML file with vanilla JS.

## Why

Every "note-taking app" I tried wanted to sync my thoughts to someone else's server. This one lives on mine. Notes are stored as plain markdown files, so if you point Syncthing or Obsidian at your vault folder, they open there too.

## Features

### Writing
- **Block editor** — Notion-style editing: headings, bullet/numbered lists, to-do checkboxes, quotes, code blocks, tables, collapsible toggles, dividers. Everything is still plain markdown underneath
- **Slash menu** — type `/` at the start of a line to insert any block type, a calendar for the current month, or today's date
- **Markdown shortcuts** — type `# `, `- `, `1. `, `- [ ] `, `> `, or ``` and the block converts as you type
- **Inline formatting** — `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, `==highlight==`, clickable `[links](https://...)`
- **Keyboard bar** — a formatting toolbar with every block type, undo/redo, move/duplicate/delete block, indent/outdent; on iPad and Android it floats right above the on-screen keyboard
- **Undo / redo** — real history for text (`Ctrl+Z` / `Ctrl+Shift+Z`) and for pen strokes, independently per note
- **Connected notes** — `[[link]]` between notes with autocomplete, plus a backlinks panel showing what links here

### Drawing
- **One surface for text and ink** — draw directly over your notes with an Apple Pencil; the ink stays visible while you type
- **Pencil = draw, finger = scroll** — touching the page with the pencil switches to draw mode instantly and that same stroke already draws; palm rejection built in
- **Pens, marker, eraser** — six pen colors, three sizes, a translucent highlighter marker, an eraser with its own sizes, stroke undo/redo

### Planning (templates)
- **New note from a template** — daily planner, weekly planner, monthly calendar (generated for the actual month), money planner, habit tracker, goal tracker, project tracker, reading list, meal planner, workout log
- **To-do progress** — the status bar counts your checkboxes ("3/7 ✓")

### Everything else
- **Multi-user** — open signup, each account gets an isolated vault (`users/<username>/vault/`)
- **Command palette** — `Ctrl/Cmd+K` searches every note, action and template with fuzzy matching
- **Three modes** — `normal` (vim-like keys), `insert` (editing), `draw` (pen canvas)
- **Light & dark themes** — a Kanagawa-inspired dark terminal theme and a light theme, toggle in the top bar, preference saved per device
- **Offline-first** — every keystroke saves to `localStorage` immediately; syncs to the server when back online; live sync between your devices every 2s
- **Reopens where you left off** — the last note you viewed comes back after a reload
- **Local files** — open and edit a real file from your disk, or pull it into your vault as a note
- **Export** — download any note as a `.md` file, or duplicate it (ink included)
- **PWA** — installable on iOS/Android/desktop, works offline
- **Self-service account deletion** — delete your account and every note in it yourself, no need to ask
- **Plain-language [privacy & terms](https://glyph.dakshhq.com/legal)** — written by the person who runs it, not a lawyer
- **Real [contact form](https://glyph.dakshhq.com/contact)** — goes to a local log file, no third-party form service

## Security

- Passwords hashed with PBKDF2-SHA256 (200k iterations), salted, never stored in plaintext
- Sessions are random tokens in an `HttpOnly` cookie
- Brute-force guards: lockout after repeated failed logins, rate-limited signups and contact submissions
- Zero outbound network calls from the server to anywhere but your own browser — no analytics, no trackers, no third-party APIs

## Running it yourself

```bash
python server.py
# starts on port 8765
# first visit redirects to /login -> "new here? create an account"
```

Configure with environment variables:

```bash
GLYPH_PORT=8765   # port to listen on
GLYPH_HOST=0.0.0.0  # bind address
```

### Auto-start on boot (systemd)

```bash
systemctl --user status glyph   # check if running
systemctl --user restart glyph  # restart after code changes
systemctl --user stop glyph
systemctl --user start glyph
journalctl --user -u glyph -f   # live logs
```

## Files

| File | Purpose |
|------|---------|
| `server.py` | HTTP server: auth, sessions, notes API, contact form (stdlib only) |
| `index.html` | Notebook frontend (HTML + CSS + JS, one file) |
| `login.html` | Shared login/signup page |
| `legal.html` | Privacy & terms |
| `contact.html` | Contact form |
| `manifest.json` | PWA manifest |
| `sw.js` | Service worker (cache-first shell, network-first HTML) |
| `icons/` | PWA icons (192px, 512px) |
| `users.json` | `{username: {salt, hash}}` — gitignored, created at runtime |
| `users/<username>/vault/` | That user's markdown notes — gitignored |

## Keyboard shortcuts

Press `?` (when not typing) or search "keyboard shortcuts" in the palette to see this list in-app.

| Key | Action |
|-----|--------|
| `Ctrl/Cmd+K` | Search notes + commands |
| `Ctrl/Cmd+Z` / `Ctrl/Cmd+Shift+Z` | Undo / redo (text, or strokes in draw mode) |
| `Ctrl/Cmd+S` | Save now |
| `Ctrl/Cmd+E` | Toggle draw / write |
| `Ctrl/Cmd+B` / `I` / `Shift+X` | Bold / italic / strikethrough (select text first) |
| `Ctrl/Cmd+D` | Duplicate block |
| `Ctrl/Cmd+Enter` | Check / uncheck a to-do |
| `Ctrl/Cmd+Alt+0–3` | Paragraph / heading 1–3 |
| `Alt+↑` / `Alt+↓` | Move block up / down |
| `Tab` / `Shift+Tab` | Indent / outdent a list item |
| `/` | Block menu (at the start of a line) |
| `[[` | Link to another note |
| `o` / `i` / `d` | New note / insert / draw (when not typing) |
| `?` | Shortcut help |
| `Esc` | Back to normal mode |

## Devices this runs on

- Desktop (Fedora/Hyprland) — local dev via `localhost:8765`
- iPad — Apple Pencil drawing, installed as a PWA via Safari
- Android — installed as a PWA via Chrome
- Production — Hetzner VPS, Caddy reverse proxy with automatic HTTPS
