# glyph

A self-hosted, multi-user, terminal-styled notebook. Plain markdown notes on the left, Apple Pencil drawing on the right — no accounts with a company behind them, no analytics, no dependencies.

Live at **[glyph.dakshhq.com](https://glyph.dakshhq.com)** — open signup, anyone can create an account and gets their own private vault.

**Stack:** Python 3 stdlib only. No dependencies, no build step, no npm, no framework. Frontend is a single HTML file with vanilla JS.

## Why

Every "note-taking app" I tried wanted to sync my thoughts to someone else's server. This one lives on mine. Notes are stored as plain markdown files, so if you point Syncthing or Obsidian at your vault folder, they open there too.

## Features

- **Multi-user** — open signup, each account gets an isolated vault (`users/<username>/vault/`)
- **Three modes** — `normal` (vim-like keys), `insert` (markdown editing), `draw` (Apple Pencil / touch canvas)
- **Light & dark themes** — a Kanagawa-inspired dark terminal theme and a light theme, toggle in the top bar, preference saved per device
- **Offline-first** — every keystroke saves to `localStorage` immediately; syncs to the server when back online
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

## Keyboard shortcuts (normal mode)

| Key | Action |
|-----|--------|
| `o` | New note |
| `i` | Edit (insert mode) |
| `d` | Draw mode |
| `Esc` | Back to normal mode |

## Devices this runs on

- Desktop (Fedora/Hyprland) — local dev via `localhost:8765`
- iPad — Apple Pencil drawing, installed as a PWA via Safari
- Android — installed as a PWA via Chrome
- Production — Hetzner VPS, Caddy reverse proxy with automatic HTTPS
