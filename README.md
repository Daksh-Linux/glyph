# glyph

A self-hosted terminal-styled notebook web app. Stores notes as plain markdown files in an Obsidian vault. Supports text editing and Apple Pencil drawing.

**Stack:** Python 3 stdlib only — no dependencies, no build step, no npm.

## Running

```bash
python ~/glyph/server.py
# server starts on port 8765
```

Access at `http://localhost:8765` or via Tailscale IP.

## Auto-start on boot (systemd)

The server is set up as a systemd user service so it starts automatically on boot.

```bash
systemctl --user status glyph   # check if running
systemctl --user restart glyph  # restart after code changes
systemctl --user stop glyph     # stop
systemctl --user start glyph    # start
journalctl --user -u glyph -f   # live logs
```

Service file: `~/.config/systemd/user/glyph.service`

## Files

| File | Purpose |
|------|---------|
| `server.py` | HTTP server (Python stdlib only) |
| `index.html` | Entire frontend (HTML + CSS + JS) |
| `manifest.json` | PWA manifest |
| `sw.js` | Service worker (cache-first shell, network-first HTML) |
| `icons/` | PWA icons (192px, 512px) |
| `vault/` | Markdown note files |
| `vault/.glyph/` | JSON stroke sidecars for drawings |

## Devices

- **Arch desktop** — runs the server, accesses via `localhost:8765`
- **iPad** — Apple Pencil drawing, PWA installed via Safari
- **Android (Pixel)** — PWA via Chrome

## Keyboard shortcuts (normal mode)

| Key | Action |
|-----|--------|
| `o` | New note |
| `i` | Edit (insert mode) |
| `d` | Draw mode |
| `Esc` | Back to normal mode |
