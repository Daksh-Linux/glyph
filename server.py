#!/usr/bin/env python3
"""glyph - self-hosted terminal notebook server (standard library only).

Notes are stored as Obsidian-compatible markdown files inside a vault
folder, so the same notes open in Obsidian on every device the folder is
synced to (Syncthing / Obsidian Sync). Apple Pencil sketches are kept as
small JSON sidecars under <vault>/.glyph/ and stay editable in glyph.

Run:
    python server.py [VAULT_DIR]

Or configure with environment variables:
    GLYPH_VAULT   path to the vault folder   (default: ./vault next to this file)
    GLYPH_PORT    port to listen on          (default: 8765)
    GLYPH_HOST    bind address               (default: 0.0.0.0, i.e. whole LAN)
"""

import hashlib
import json
import os
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(
    os.environ.get("GLYPH_VAULT")
    or (sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "vault"))
)
INK = os.path.join(VAULT, ".glyph")
HOST = os.environ.get("GLYPH_HOST", "0.0.0.0")
PORT = int(os.environ.get("GLYPH_PORT", "8765"))

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def ensure_dirs():
    os.makedirs(VAULT, exist_ok=True)
    os.makedirs(INK, exist_ok=True)


def slugify(title):
    cleaned = UNSAFE_NAME.sub("", (title or "").strip()).strip(". ")
    return (cleaned or "untitled")[:80]


def sidecar(note_id):
    safe = re.sub(r"[^A-Za-z0-9]", "", note_id)[:32] or "x"
    return os.path.join(INK, safe + ".json")


def parse(path):
    """Read a markdown note. Title is the filename (Obsidian convention);
    id, updated, and parent come from YAML frontmatter when present."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    note_id = None
    updated = None
    parent = None
    body = raw
    match = FRONTMATTER.match(raw)
    if match:
        body = raw[match.end():]
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                key, value = key.strip(), value.strip()
                if key == "id":
                    note_id = value
                elif key == "updated":
                    try:
                        updated = int(float(value))
                    except ValueError:
                        pass
                elif key == "parent":
                    parent = value if value else None
    title = os.path.basename(path)[:-3]
    if not note_id:
        # adopt notes created in Obsidian: derive a stable id from the path
        rel = os.path.relpath(path, VAULT)
        note_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    if updated is None:
        updated = int(os.path.getmtime(path))
    return {"id": note_id, "title": title, "updated": updated, "body": body, "parent": parent}


def md_files():
    out = []
    for name in sorted(os.listdir(VAULT)):
        if name.endswith(".md") and not name.startswith("."):
            out.append(os.path.join(VAULT, name))
    return out


def find_path(note_id):
    for path in md_files():
        try:
            if parse(path)["id"] == note_id:
                return path
        except OSError:
            continue
    return None


def unique_name(title, note_id):
    base = slugify(title)
    candidate = base + ".md"
    i = 2
    while True:
        path = os.path.join(VAULT, candidate)
        if not os.path.exists(path):
            return candidate
        try:
            if parse(path)["id"] == note_id:
                return candidate  # this file already belongs to the note
        except OSError:
            pass
        candidate = "%s-%d.md" % (base, i)
        i += 1


def list_meta():
    items = []
    for path in md_files():
        try:
            note = parse(path)
        except OSError:
            continue
        items.append({
            "id": note["id"],
            "title": note["title"],
            "updated": note["updated"],
            "hasInk": os.path.exists(sidecar(note["id"])),
            "parent": note.get("parent"),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def read_note(note_id):
    path = find_path(note_id)
    if not path:
        return None
    note = parse(path)
    strokes = []
    side = sidecar(note["id"])
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                strokes = json.load(f)
        except (OSError, ValueError):
            strokes = []
    note["strokes"] = strokes
    return note


def write_note(note_id, title, body, strokes, parent=None):
    old = find_path(note_id)
    name = unique_name(title, note_id)
    path = os.path.join(VAULT, name)
    header = "---\nid: %s\nupdated: %d\n" % (note_id, int(time.time()))
    if parent:
        header += "parent: %s\n" % parent
    header += "---\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + (body or ""))
    if old and os.path.abspath(old) != os.path.abspath(path) and os.path.exists(old):
        try:
            os.remove(old)  # title changed -> rename by removing the old file
        except OSError:
            pass
    side = sidecar(note_id)
    if strokes:
        with open(side, "w", encoding="utf-8") as f:
            json.dump(strokes, f, separators=(",", ":"))
    elif os.path.exists(side):
        try:
            os.remove(side)
        except OSError:
            pass
    return parse(path)


def delete_note(note_id):
    path = find_path(note_id)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    side = sidecar(note_id)
    if os.path.exists(side):
        try:
            os.remove(side)
        except OSError:
            pass


SEED = """\
# readme

a terminal you can write in. plain text on the left, apple pencil on the right.

## modes

- NORMAL  browse notes / run keys
- INSERT  type markdown        (key: i)
- DRAW    sketch with a pencil (key: d)
- new note                     (key: o)
- back to NORMAL               (key: esc)

## sync

these notes are plain markdown files in your vault, so they open in
Obsidian on every device the folder is synced to. sketches are saved
next to them under .glyph/ and stay editable back here in glyph.

start typing, or hit  d  to draw.
"""


def write_seed():
    if not md_files():
        write_note("readme00", "readme", SEED, [])


class Handler(BaseHTTPRequestHandler):
    server_version = "glyph/1.0"

    def log_message(self, *args):
        pass  # keep the console quiet

    def _json(self, obj, code=200):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _serve_index(self):
        path = os.path.join(HERE, "index.html")
        try:
            with open(path, "rb") as f:
                data = f.read()
            ctype = "text/html; charset=utf-8"
        except OSError:
            data = b"index.html was not found next to server.py"
            ctype = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # never cache HTML — always fetch fresh
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, url_path):
        # map URL path to a file inside the app directory — strip leading slash and block traversal
        safe = url_path.lstrip("/").replace("..", "")
        full = os.path.join(HERE, safe)
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(full)[1].lower()
        ctypes = {
            ".json": "application/json; charset=utf-8",
            ".js":   "text/javascript; charset=utf-8",
            ".png":  "image/png",
            ".svg":  "image/svg+xml",
            ".ico":  "image/x-icon",
        }
        ctype = ctypes.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_index()
        if path == "/api/notes":
            return self._json(list_meta())
        match = re.match(r"^/api/notes/([^/]+)$", path)
        if match:
            note = read_note(unquote(match.group(1)))
            return self._json(note) if note else self._json({"error": "not found"}, 404)
        # fall back to serving static files (manifest, sw.js, icons, etc.)
        self._serve_static(path)

    def do_PUT(self):
        match = re.match(r"^/api/notes/([^/]+)$", urlparse(self.path).path)
        if not match:
            return self._json({"error": "not found"}, 404)
        try:
            data = self._read_body()
            note = write_note(
                unquote(match.group(1)),
                data.get("title", "untitled"),
                data.get("body", ""),
                data.get("strokes", []),
                data.get("parent") or None,
            )
            self._json(note)
        except Exception as err:  # noqa: BLE001 - report any failure to the client
            self._json({"error": str(err)}, 500)

    def do_DELETE(self):
        match = re.match(r"^/api/notes/([^/]+)$", urlparse(self.path).path)
        if not match:
            return self._json({"error": "not found"}, 404)
        delete_note(unquote(match.group(1)))
        self.send_response(204)
        self.end_headers()


def lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    ensure_dirs()
    write_seed()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    ip = lan_ip()
    print("")
    print("  glyph notebook")
    print("  vault : %s" % VAULT)
    print("  local : http://%s:%d" % (ip, PORT))
    print("  open that URL in a browser on any device on your network")
    print("  ctrl-c to stop")
    print("")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")
        httpd.shutdown()


if __name__ == "__main__":
    main()
