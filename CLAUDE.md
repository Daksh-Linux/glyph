# glyph — project context for Claude

## What this is
A self-hosted terminal-styled notebook web app. Daksh runs it on his Arch Linux desktop and accesses it from his iPad and Android phone. Notes are stored as plain markdown files in an Obsidian vault.

**Stack:** Python 3 stdlib only (no dependencies), single-file server + single-file frontend. No build step, no npm, no framework.

**Files:**
- `server.py` — HTTP server (Python stdlib only)
- `index.html` — entire frontend (HTML + CSS + JS, one file)
- `manifest.json` — PWA manifest
- `sw.js` — service worker (cache-first for shell, network-first for HTML)
- `icons/192.png`, `icons/512.png` — PWA icons (generated via Python stdlib, no PIL)

## How to run
```bash
python ~/glyph/server.py
# server starts on port 8765
# access at http://100.83.162.42:8765 (Tailscale) or LAN IP
```

To restart after code changes:
```bash
pkill -f server.py && python ~/glyph/server.py &
```

## Devices
- **Arch desktop** — runs the server, accesses via localhost:8765
- **iPad** — Apple Pencil drawing, PWA installed via Safari (Add to Home Screen)
- **Android phone** — Pixel, PWA via Chrome

## Architecture

### Server (server.py)
- `do_GET` — serves `/` (index.html), `/api/notes`, `/api/notes/<id>`, static files
- `do_PUT` — saves a note (title + body + strokes)
- `do_DELETE` — deletes a note
- `_serve_index()` — reads and serves index.html with `Cache-Control: no-store`
- `_serve_static()` — serves manifest, sw.js, icons with path traversal protection
- Notes stored as markdown with YAML frontmatter (`id`, `updated`) in vault directory
- Strokes stored as JSON sidecars in `<vault>/.glyph/<id>.json`

### Frontend (index.html)
Single file, no build step. Key sections:

**Modes:** `normal` (vim-like, keyboard shortcuts), `insert` (text editing), `draw` (canvas)

**Color theme (Kanagawa dark):**
```
--ink:#16161d  --surface:#1f1f28  --panel:#22222c
--text:#dcd7ba  --blue:#7e9cd8  --green:#98bb6c
--yellow:#e6c384  --red:#e46876  --mauve:#957fb8
```

**Canvas (draw mode):**
- Two-canvas architecture: `#cv` (base, committed strokes) + `#cvlive` (current stroke only)
- Bezier curves via `quadraticCurveTo` for smooth strokes
- Eraser uses `globalCompositeOperation = "destination-out"`
- Pen sizes: 2/3.5/6px. Eraser sizes: 10/22/44px
- `touch-action: none` everywhere; manual JS scroll for finger scroll in pen-only mode
- Momentum scrolling with 0.88 velocity decay via rAF

**Text editor:**
- Plain `<textarea class="editor">` with `flex:1` — no wrapper div
- Font size 16px (required to prevent Android auto-zoom)
- `user-select: text; -webkit-user-select: text` for Android text selection
- Auto-saves 650ms after last keystroke

**Offline support:**
- Every keystroke saves to `localStorage` immediately (data never lost)
- `lcMarkPending(id)` — tracks notes not yet pushed to server
- `syncPending()` — flushes local queue when server comes back
- Reconnect detection: polls every 3s when offline
- Status bar shows: `synced` (green) / `saving` (yellow) / `offline` (yellow) / `save failed` (red)

**Live sync:**
- `pollActiveNote()` runs every 2s when online
- Compares `updated` timestamp; pulls content if server has newer version
- Skips if user typed within last 2s (avoids clobbering in-progress input)

**PWA:**
- `manifest.json` with `display: standalone`
- Service worker `sw.js` (cache name `glyph-v2`) — bump version when changing SW behavior
- iOS: `env(safe-area-inset-top)` for status bar blending
- `color-scheme: dark` in both CSS and meta tag — prevents Android forced-dark mode

## Key bugs already fixed
- **Android scrim bug**: `.scrim{display:block}` in mobile media query overrode `[hidden]` attribute — fixed with `[hidden]{display:none!important}`. This was blocking all touches on Android (no keyboard, no text selection, wrong colors).
- **iPad pen scroll**: `touch-action: none` + manual JS scroll so pen doesn't scroll, fingers do.
- **SW caching old HTML**: Bump `CACHE` version in `sw.js` whenever you need clients to get fresh code.
- **Android keyboard**: `font-size: 16px` (prevents auto-zoom), no `maximum-scale=1` in viewport, no DOM changes during focus event (deferred 100ms).

## What Daksh wants
- Learning web dev (HTML/CSS/JS/Python) while building — explain what code does when adding new things
- Keep it simple: no frameworks, no build steps, no dependencies
- Works well on all three devices (Arch, iPad with Apple Pencil, Android phone)
- Terminal/vim aesthetic (Kanagawa color theme, mode indicator in status bar)
