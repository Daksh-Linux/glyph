#!/usr/bin/env python3
"""glyph - self-hosted terminal notebook server (standard library only).

Multi-user: anyone who signs up gets their own account and their own
private vault. Notes are stored as Obsidian-compatible markdown files
inside <user>'s vault folder, so the same notes open in Obsidian if that
folder is synced to another device (Syncthing / Obsidian Sync). Apple
Pencil sketches are kept as small JSON sidecars under vault/.glyph/ and
stay editable in glyph.

Run:
    python server.py

Configure with environment variables:
    GLYPH_PORT    port to listen on          (default: 8765)
    GLYPH_HOST    bind address               (default: 0.0.0.0, i.e. whole LAN)

Storage layout (both next to this file):
    users.json          {username: {salt, hash}}
    users/<username>/vault/            that user's markdown notes
    users/<username>/vault/.glyph/     that user's stroke sidecars
"""

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_ROOT = os.path.join(HERE, "users")
HOST = os.environ.get("GLYPH_HOST", "0.0.0.0")
PORT = int(os.environ.get("GLYPH_PORT", "8765"))

# --- accounts -------------------------------------------------------------
USERS_FILE = os.path.join(HERE, "users.json")
USERS_LOCK = threading.Lock()

USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,20}$")
RESERVED_USERNAMES = {"api", "login", "signup", "static", "index", "assets"}

SESSION_COOKIE = "glyph_session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
PBKDF2_ITERATIONS = 200_000
MAX_BODY_SIZE = 2 * 1024 * 1024  # 2MB — plenty for a note or a form post; blocks someone
                                  # sending a huge Content-Length to exhaust memory/CPU
                                  # (e.g. a multi-GB "password" fed into PBKDF2) or bloat a vault
LOCKOUT_THRESHOLD = 8       # failed logins from one IP before it's locked out
LOCKOUT_SECONDS = 300
SIGNUP_MAX_PER_HOUR = 5     # signups from one IP per hour — this is an open-signup
                            # site with no email verification, so this is the main
                            # brake on someone scripting mass account creation
CONTACT_FILE = os.path.join(HERE, "contact_messages.jsonl")
CONTACT_MAX_PER_HOUR = 5    # contact form submissions from one IP per hour

# Everything below is in-memory only: sessions, failed-login counts, and
# signup counts all reset when the server restarts. That just means
# everyone has to log back in after a deploy — acceptable trade-off for
# staying dependency-free (no external session store).
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
FAILED = {}
FAILED_LOCK = threading.Lock()
SIGNUPS = {}
SIGNUPS_LOCK = threading.Lock()


def valid_username(name):
    return bool(USERNAME_RE.match(name or "")) and name not in RESERVED_USERNAMES


def _hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt, digest


def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    # write-then-rename so a crash/kill mid-write can never truncate/corrupt the one file
    # that holds every account's credentials — os.replace is atomic on POSIX
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f)
    os.replace(tmp, USERS_FILE)


def user_vault(username):
    return os.path.join(USERS_ROOT, username, "vault")


def create_user(username, password):
    """Returns False if the username is already taken."""
    salt, digest = _hash_password(password)
    with USERS_LOCK:
        users = load_users()
        if username in users:
            return False
        users[username] = {"salt": salt.hex(), "hash": digest.hex()}
        save_users(users)
    vault = user_vault(username)
    os.makedirs(os.path.join(vault, ".glyph"), exist_ok=True)
    write_seed(vault)
    return True


def check_user_password(username, password):
    entry = load_users().get(username)
    if not entry:
        return False
    _, digest = _hash_password(password, bytes.fromhex(entry["salt"]))
    # compare_digest instead of == so a wrong guess can't be timed to leak how much of it matched
    return hmac.compare_digest(digest.hex(), entry["hash"])


def delete_user(username):
    """Permanently removes the account, its password entry, and its entire
    vault (notes + ink sidecars). Also drops every active session for this
    user, not just the one that requested the deletion."""
    with USERS_LOCK:
        users = load_users()
        users.pop(username, None)
        save_users(users)
    user_dir = os.path.join(USERS_ROOT, username)
    if os.path.isdir(user_dir):
        shutil.rmtree(user_dir, ignore_errors=True)
    with SESSIONS_LOCK:
        for token in [t for t, s in SESSIONS.items() if s["user"] == username]:
            del SESSIONS[token]


def new_session(username):
    token = secrets.token_hex(32)
    with SESSIONS_LOCK:
        SESSIONS[token] = {"user": username, "expiry": time.time() + SESSION_TTL}
    return token


def session_user(token):
    if not token:
        return None
    with SESSIONS_LOCK:
        entry = SESSIONS.get(token)
        if not entry:
            return None
        if entry["expiry"] < time.time():
            del SESSIONS[token]
            return None
        return entry["user"]


def drop_session(token):
    with SESSIONS_LOCK:
        SESSIONS.pop(token, None)


def note_failure(ip):
    with FAILED_LOCK:
        count, _ = FAILED.get(ip, (0, 0))
        count += 1
        until = time.time() + LOCKOUT_SECONDS if count >= LOCKOUT_THRESHOLD else 0
        FAILED[ip] = (count, until)


def is_locked(ip):
    with FAILED_LOCK:
        count, until = FAILED.get(ip, (0, 0))
        if count >= LOCKOUT_THRESHOLD and time.time() < until:
            return True
        if until and time.time() >= until:
            FAILED.pop(ip, None)
        return False


def clear_failures(ip):
    with FAILED_LOCK:
        FAILED.pop(ip, None)


def is_signup_locked(ip):
    with SIGNUPS_LOCK:
        attempts = [t for t in SIGNUPS.get(ip, []) if time.time() - t < 3600]
        SIGNUPS[ip] = attempts
        return len(attempts) >= SIGNUP_MAX_PER_HOUR


def note_signup_attempt(ip):
    with SIGNUPS_LOCK:
        SIGNUPS.setdefault(ip, []).append(time.time())


# --- contact form ----------------------------------------------------------
CONTACTS = {}
CONTACTS_LOCK = threading.Lock()


def is_contact_locked(ip):
    with CONTACTS_LOCK:
        attempts = [t for t in CONTACTS.get(ip, []) if time.time() - t < 3600]
        CONTACTS[ip] = attempts
        return len(attempts) >= CONTACT_MAX_PER_HOUR


def note_contact_attempt(ip):
    with CONTACTS_LOCK:
        CONTACTS.setdefault(ip, []).append(time.time())


def save_contact_message(name, email, message, ip):
    # appended, never overwritten — read with: ssh ... cat contact_messages.jsonl
    entry = {
        "time": int(time.time()),
        "name": name[:200],
        "email": email[:200],
        "message": message[:4000],
        "ip": ip,
    }
    with CONTACTS_LOCK:
        with open(CONTACT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


# --- notes ------------------------------------------------------------
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def extract_links(body):
    """[[Some Title]] -> ["Some Title", ...], in the order they appear."""
    return [m.strip() for m in WIKILINK.findall(body or "")]


def slugify(title):
    cleaned = UNSAFE_NAME.sub("", (title or "").strip()).strip(". ")
    return (cleaned or "untitled")[:80]


def sidecar(vault, note_id):
    safe = re.sub(r"[^A-Za-z0-9]", "", note_id)[:32] or "x"
    return os.path.join(vault, ".glyph", safe + ".json")


def parse(path, vault):
    """Read a markdown note. Title is the filename (Obsidian convention);
    id, updated, and parent come from YAML frontmatter when present."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    note_id = None
    updated = None
    parent = None
    pinned = False
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
                elif key == "pinned":
                    pinned = value.lower() in ("true", "1", "yes")
    title = os.path.basename(path)[:-3]
    if not note_id:
        # adopt notes created in Obsidian: derive a stable id from the path
        rel = os.path.relpath(path, vault)
        note_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    if updated is None:
        updated = int(os.path.getmtime(path))
    return {"id": note_id, "title": title, "updated": updated, "body": body,
            "parent": parent, "pinned": pinned}


def md_files(vault):
    out = []
    for name in sorted(os.listdir(vault)):
        if name.endswith(".md") and not name.startswith("."):
            out.append(os.path.join(vault, name))
    return out


def find_path(vault, note_id):
    for path in md_files(vault):
        try:
            if parse(path, vault)["id"] == note_id:
                return path
        except OSError:
            continue
    return None


def backlinks(vault, note_id):
    """Two kinds of incoming references, Obsidian-style:
    - linked: notes whose body has a real [[link]] to this note's title
    - mentions: notes whose body contains the title as plain text but never
      linked it (skipped for very short titles — they'd match everything)."""
    target_path = find_path(vault, note_id)
    if not target_path:
        return {"linked": [], "mentions": []}
    target_title = os.path.basename(target_path)[:-3].lower()
    linked, mentions = [], []
    for path in md_files(vault):
        if path == target_path:
            continue
        try:
            note = parse(path, vault)
        except OSError:
            continue
        titles = {t.lower() for t in extract_links(note["body"])}
        if target_title in titles:
            linked.append({"id": note["id"], "title": note["title"]})
        elif len(target_title) >= 4 and target_title in note["body"].lower():
            mentions.append({"id": note["id"], "title": note["title"]})
    return {"linked": linked, "mentions": mentions}


def unique_name(vault, title, note_id):
    base = slugify(title)
    candidate = base + ".md"
    i = 2
    while True:
        path = os.path.join(vault, candidate)
        if not os.path.exists(path):
            return candidate
        try:
            if parse(path, vault)["id"] == note_id:
                return candidate  # this file already belongs to the note
        except OSError:
            pass
        candidate = "%s-%d.md" % (base, i)
        i += 1


def list_meta(vault):
    items = []
    for path in md_files(vault):
        try:
            note = parse(path, vault)
        except OSError:
            continue
        items.append({
            "id": note["id"],
            "title": note["title"],
            "updated": note["updated"],
            "hasInk": os.path.exists(sidecar(vault, note["id"])),
            "parent": note.get("parent"),
            "pinned": note.get("pinned", False),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def search_notes(vault, query, limit=20):
    """Case-insensitive full-text search over every note's title + body.
    Returns a one-line snippet around the first body match so the palette
    can show WHY a note matched, not just that it did."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    out = []
    for path in md_files(vault):
        try:
            note = parse(path, vault)
        except OSError:
            continue
        if q not in (note["title"] + "\n" + note["body"]).lower():
            continue
        snippet = ""
        for line in note["body"].splitlines():
            if q in line.lower():
                snippet = line.strip()
                pos = snippet.lower().find(q)
                if len(snippet) > 90:
                    start = max(0, pos - 30)
                    snippet = ("…" if start else "") + snippet[start:start + 90] + "…"
                break
        out.append({"id": note["id"], "title": note["title"], "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def graph_data(vault):
    """Nodes = every note; links = resolved [[wiki-links]] between them (by title,
    same resolution rule as backlinks). Feeds the graph-view modal."""
    metas, bodies = [], {}
    for path in md_files(vault):
        try:
            note = parse(path, vault)
        except OSError:
            continue
        metas.append({"id": note["id"], "title": note["title"]})
        bodies[note["id"]] = note["body"]
    by_title = {m["title"].lower(): m["id"] for m in metas}
    links = []
    for m in metas:
        for t in extract_links(bodies[m["id"]]):
            tid = by_title.get(t.lower())
            if tid and tid != m["id"]:
                links.append({"source": m["id"], "target": tid})
    return {"nodes": metas, "links": links}


def vault_stats(vault):
    """Whole-vault numbers for the stats modal — notes, words, to-dos, ink, links."""
    notes = words = todos_total = todos_done = ink = links = 0
    for path in md_files(vault):
        try:
            note = parse(path, vault)
        except OSError:
            continue
        notes += 1
        body = note["body"]
        words += len(body.split())
        todos_total += len(re.findall(r"^\s*- \[[ xX]\]", body, re.M))
        todos_done += len(re.findall(r"^\s*- \[[xX]\]", body, re.M))
        links += len(extract_links(body))
        if os.path.exists(sidecar(vault, note["id"])):
            ink += 1
    return {"notes": notes, "words": words, "todosTotal": todos_total,
            "todosDone": todos_done, "ink": ink, "links": links,
            "trash": len(trash_files(vault))}


def export_zip(vault):
    """The whole vault (notes + ink sidecars) as one zip, built in memory.
    Notes are already plain markdown, so the export is Obsidian-ready as-is."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in md_files(vault):
            z.write(path, os.path.basename(path))
        glyph_dir = os.path.join(vault, ".glyph")
        if os.path.isdir(glyph_dir):
            for name in sorted(os.listdir(glyph_dir)):
                if name.endswith(".json"):
                    z.write(os.path.join(glyph_dir, name), ".glyph/" + name)
    return buf.getvalue()


def read_note(vault, note_id):
    path = find_path(vault, note_id)
    if not path:
        return None
    note = parse(path, vault)
    strokes = []
    side = sidecar(vault, note["id"])
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                strokes = json.load(f)
        except (OSError, ValueError):
            strokes = []
    note["strokes"] = strokes
    return note


def would_create_cycle(vault, note_id, parent_id):
    """True if note_id is already among parent_id's own ancestors — i.e. setting
    note_id's parent to parent_id would close a loop. Walks up via each note's stored
    parent, bounded by a visited set in case a cycle already exists in the data (e.g.
    hand-edited frontmatter from Obsidian)."""
    seen = set()
    current = parent_id
    while current and current not in seen:
        if current == note_id:
            return True
        seen.add(current)
        path = find_path(vault, current)
        current = parse(path, vault).get("parent") if path else None
    return False


def write_note(vault, note_id, title, body, strokes, parent=None, pinned=False):
    if parent and would_create_cycle(vault, note_id, parent):
        parent = None  # refuse to create/preserve a parent cycle — drop to root instead
    old = find_path(vault, note_id)
    name = unique_name(vault, title, note_id)
    path = os.path.join(vault, name)
    header = "---\nid: %s\nupdated: %d\n" % (note_id, int(time.time()))
    if parent:
        header += "parent: %s\n" % parent
    if pinned:
        header += "pinned: true\n"
    header += "---\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + (body or ""))
    if old and os.path.abspath(old) != os.path.abspath(path) and os.path.exists(old):
        try:
            os.remove(old)  # title changed -> rename by removing the old file
        except OSError:
            pass
    side = sidecar(vault, note_id)
    if strokes:
        with open(side, "w", encoding="utf-8") as f:
            json.dump(strokes, f, separators=(",", ":"))
    elif os.path.exists(side):
        try:
            os.remove(side)
        except OSError:
            pass
    return parse(path, vault)


# --- trash ------------------------------------------------------------
# Deleting a note moves it to vault/.trash/ instead of unlinking it, like
# Notion's trash. md_files() never lists dot-directories, so trashed notes
# vanish from every listing/search/backlink automatically. Auto-purged
# after 30 days (checked lazily whenever the trash is listed).
TRASH_KEEP_DAYS = 30


def trash_dir(vault):
    d = os.path.join(vault, ".trash")
    os.makedirs(d, exist_ok=True)
    return d


def trash_files(vault):
    d = os.path.join(vault, ".trash")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".md")]


def delete_note(vault, note_id):
    path = find_path(vault, note_id)
    td = trash_dir(vault)
    if path and os.path.exists(path):
        dest = os.path.join(td, os.path.basename(path))
        base, i = os.path.basename(path)[:-3], 2
        while os.path.exists(dest):
            dest = os.path.join(td, "%s-%d.md" % (base, i))
            i += 1
        try:
            os.replace(path, dest)
            os.utime(dest, None)  # rename keeps the old mtime; touch it so the 30-day
                                  # purge clock starts at DELETION time, not creation time
        except OSError:
            pass
    side = sidecar(vault, note_id)
    if os.path.exists(side):
        try:
            os.replace(side, os.path.join(td, os.path.basename(side)))
        except OSError:
            pass


def purge_old_trash(vault):
    cutoff = time.time() - TRASH_KEEP_DAYS * 86400
    d = os.path.join(vault, ".trash")
    if not os.path.isdir(d):
        return
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def list_trash(vault):
    purge_old_trash(vault)
    items = []
    for path in trash_files(vault):
        try:
            note = parse(path, vault)
        except OSError:
            continue
        items.append({"id": note["id"], "title": note["title"],
                      "deleted": int(os.path.getmtime(path))})
    items.sort(key=lambda x: x["deleted"], reverse=True)
    return items


def find_trash_path(vault, note_id):
    for path in trash_files(vault):
        try:
            if parse(path, vault)["id"] == note_id:
                return path
        except OSError:
            continue
    return None


def restore_note(vault, note_id):
    path = find_trash_path(vault, note_id)
    if not path:
        return None
    note = parse(path, vault)
    name = unique_name(vault, note["title"], note_id)  # a live note may have taken the title meanwhile
    os.replace(path, os.path.join(vault, name))
    trashed_side = os.path.join(vault, ".trash", os.path.basename(sidecar(vault, note_id)))
    if os.path.exists(trashed_side):
        try:
            os.replace(trashed_side, sidecar(vault, note_id))
        except OSError:
            pass
    return parse(os.path.join(vault, name), vault)


def purge_note(vault, note_id):
    """Delete forever — removes the note (and its ink) from the trash."""
    path = find_trash_path(vault, note_id)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    trashed_side = os.path.join(vault, ".trash", os.path.basename(sidecar(vault, note_id)))
    if os.path.exists(trashed_side):
        try:
            os.remove(trashed_side)
        except OSError:
            pass


SEED = """\
# readme — how to use glyph

welcome! glyph is a notebook that's plain markdown underneath, drawable on
top, and lives in your own private vault. this note is the manual — it's
just a note, so edit it, draw on it, or delete it whenever you like.

> [!tip] the three things worth learning first
> type `/` at the start of any line for the block menu · tap **+** for a
> new note or a template · search everything with the bar up top (Ctrl-K)

## where everything is

- **top bar** — menu (opens the sidebar), the search bar (notes, commands,
  and full-text search inside every note), insert/draw switches, + new
  note, theme toggle, log out
- **sidebar** (☰ on a phone) — search, **+ new note**, ☀ today's daily
  note, your pinned notes, the note tree, and at the bottom: templates,
  trash, export vault, graph view, open file, and **help** — the full
  guide to everything, searchable
- **⋯ next to any note** — open, add a note inside, pin, duplicate,
  move, copy link, download, delete
- **bar above the keyboard** — every block type, bold/italic, indent,
  move block up/down, undo/redo. it floats on top of the phone keyboard
- **status bar** (bottom) — current mode, note name, to-do progress,
  sync state

## writing

type markdown and it becomes real formatting as you go:

- `# ` through `###### `  headings
- `- ` bullets · `1. ` numbered · `- [ ] ` to-dos
- `> ` quote · `> [!tip] ` callout · `---` divider
- three backticks for a code block (language label + copy button included)

or type `/` on an empty line and pick from the menu instead — on a phone
the menu floats above the keyboard, and you can drag it by its grip to
wherever you want it (double-tap the grip to reset).

- [ ] this is a to-do — the status bar is counting it right now
- [ ] tables move like a spreadsheet: Tab next cell, Enter next row

select text and a floating toolbar appears: **bold**, *italic*,
~~strike~~, `code`, ==highlight==, or turn the selection into a [[link]].
paste a URL over selected text and it becomes a link. `:fire:` becomes 🔥.
Shift-Enter makes a line break inside the same paragraph.

undo is real everywhere — Ctrl/Cmd-Z for text, the same keys (or the
toolbar buttons) for pen strokes while drawing.

## drawing

touch the page with an apple pencil and you're instantly drawing — same
page as your text, no separate screen. fingers still scroll. in the draw
toolbar: fifteen pen colours, three sizes, a translucent marker, an
eraser, undo/redo/clear, and **done** to go back to typing. pen-only is
on by default so your resting palm never draws.

tap **select** and circle some writing with the pencil to lasso it, then
drag it anywhere, scale it with A+ / A−, highlight it, recolor it,
duplicate it, or delete it.

## linking & finding

- type `[[` to link to another note — pick from the popup as you type
- backlinks and unlinked mentions appear at the bottom of the linked note
- `#tags` are tappable — one tap searches that tag everywhere
- the search bar looks **inside** every note, with a snippet of the match
- **graph view** (sidebar) draws your whole vault as a map — tap a dot
- pin notes ★, nest notes inside notes, drag blocks by the ⋮⋮ handle

## planners & templates

tap **+** and pick: daily planner, weekly planner, money planner, habit
tracker, monthly calendar (a real calendar for this month), project
tracker, travel planner, reading list, meal planner, workout log, study
notes, weekly review — or save any note of yours as a template ("save
note as template" in the search bar).

## everything in the search bar

type what you want into the search bar — it's also the command menu:
undo · redo · zen mode · print / save as PDF · note info · vault stats ·
outline · random note · copy link · duplicate · move note · check all
to-dos · small text · narrow page · spellcheck · sort sidebar · trash ·
export — if glyph can do it, it's in there.

## your data is yours

notes are plain markdown files. point Obsidian or Syncthing at your vault
folder and everything opens there too. download any note as .md or the
whole vault as .zip from the sidebar. deleted notes wait in trash for 30
days. no lock-in, no analytics, no third parties.

press ? — or open sidebar → help — for the searchable guide to every
feature and shortcut. start typing, or touch the page with a pencil.
"""


def write_seed(vault):
    if not md_files(vault):
        write_note(vault, "readme00", "readme", SEED, [])


class Handler(BaseHTTPRequestHandler):
    server_version = "glyph/1.0"

    def log_message(self, *args):
        pass  # keep the console quiet

    def _json(self, obj, code=200, headers=None):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or []):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _reject_oversized(self):
        """Checked before reading any request body — protocol_version defaults to HTTP/1.0
        here (one connection per request), so refusing without draining the body is fine,
        no keep-alive state to corrupt."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_SIZE:
            self._json({"error": "request too large"}, 413)
            return True
        return False

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _current_user(self):
        return session_user(self._session_token())

    def _set_cookie_header(self, token):
        return ("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" %
                (SESSION_COOKIE, token, SESSION_TTL))

    def _clear_cookie_header(self):
        return ("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % SESSION_COOKIE)

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

    def _serve_auth_page(self, filename, mode):
        # login.html renders both the login and signup forms; the __MODE__
        # marker tells its JS which copy/fields to show.
        path = os.path.join(HERE, filename)
        try:
            with open(path, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            html = "<p>%s was not found next to server.py</p>" % filename
        html = html.replace("__MODE__", mode)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
            ".html": "text/html; charset=utf-8",
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
        user = self._current_user()

        if path == "/login":
            if user:
                return self._redirect("/")
            return self._serve_auth_page("login.html", "login")
        if path == "/signup":
            if user:
                return self._redirect("/")
            return self._serve_auth_page("login.html", "signup")
        if path in ("/legal", "/privacy", "/terms"):
            # public on purpose — has to be readable before someone signs up, not gated behind login
            return self._serve_static("legal.html")
        if path == "/contact":
            # public on purpose — same reasoning as /legal
            return self._serve_static("contact.html")
        if path == "/manifest.json" or path == "/sw.js" or path.startswith("/icons/"):
            # public on purpose — favicons and the PWA manifest have to load on /login too,
            # before anyone has a session. sw.js MUST be public: a logged-out device with a
            # broken cached service worker can only heal itself by fetching the fixed one,
            # and it can't log in through a broken worker. Nothing private in any of these.
            return self._serve_static(path)
        if path in ("/", "/index.html"):
            if not user:
                return self._redirect("/login")
            return self._serve_index()
        if not user:
            return self._json({"error": "unauthorized"}, 401)
        if path == "/api/me":
            return self._json({"username": user})
        if path == "/api/notes":
            return self._json(list_meta(user_vault(user)))
        if path == "/api/search":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            return self._json(search_notes(user_vault(user), q))
        if path == "/api/trash":
            return self._json(list_trash(user_vault(user)))
        if path == "/api/graph":
            return self._json(graph_data(user_vault(user)))
        if path == "/api/stats":
            return self._json(vault_stats(user_vault(user)))
        if path == "/api/export.zip":
            data = export_zip(user_vault(user))
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="glyph-notes.zip"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        match = re.match(r"^/api/notes/([^/]+)/backlinks$", path)
        if match:
            return self._json(backlinks(user_vault(user), unquote(match.group(1))))
        match = re.match(r"^/api/notes/([^/]+)$", path)
        if match:
            note = read_note(user_vault(user), unquote(match.group(1)))
            return self._json(note) if note else self._json({"error": "not found"}, 404)
        # fall back to serving static files (manifest, sw.js, icons, etc.)
        self._serve_static(path)

    def do_POST(self):
        if self._reject_oversized():
            return
        path = urlparse(self.path).path
        ip = self.client_address[0]

        if path == "/api/auth/signup":
            if is_signup_locked(ip):
                return self._json({"error": "too many accounts created from this network, try again later"}, 429)
            data = self._read_body()
            username = (data.get("username") or "").strip().lower()
            password = data.get("password") or ""
            if not valid_username(username):
                return self._json({"error": "username must be 3-20 characters: lowercase letters, numbers, - or _"}, 400)
            if len(password) < 8:
                return self._json({"error": "password must be at least 8 characters"}, 400)
            note_signup_attempt(ip)
            if not create_user(username, password):
                return self._json({"error": "that username is already taken"}, 400)
            token = new_session(username)
            return self._json({"ok": True}, headers=[self._set_cookie_header(token)])

        if path == "/api/auth/login":
            if is_locked(ip):
                return self._json({"error": "too many attempts, try again in a few minutes"}, 429)
            data = self._read_body()
            username = (data.get("username") or "").strip().lower()
            password = data.get("password") or ""
            if check_user_password(username, password):
                clear_failures(ip)
                token = new_session(username)
                return self._json({"ok": True}, headers=[self._set_cookie_header(token)])
            note_failure(ip)
            time.sleep(1)  # slow down brute-force guessing
            return self._json({"error": "wrong username or password"}, 401)

        if path == "/api/auth/logout":
            drop_session(self._session_token())
            return self._json({"ok": True}, headers=[self._clear_cookie_header()])

        # restoring from trash needs a session, unlike the public auth/contact routes above
        match = re.match(r"^/api/trash/([^/]+)/restore$", path)
        if match:
            user = self._current_user()
            if not user:
                return self._json({"error": "unauthorized"}, 401)
            note = restore_note(user_vault(user), unquote(match.group(1)))
            return self._json(note) if note else self._json({"error": "not found"}, 404)

        if path == "/api/contact":
            if is_contact_locked(ip):
                return self._json({"error": "too many messages sent, try again later"}, 429)
            data = self._read_body()
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip()
            message = (data.get("message") or "").strip()
            if not message:
                return self._json({"error": "message can't be empty"}, 400)
            if len(message) > 4000:
                return self._json({"error": "message is too long"}, 400)
            note_contact_attempt(ip)
            save_contact_message(name, email, message, ip)
            return self._json({"ok": True})

        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        if self._reject_oversized():
            return
        user = self._current_user()
        if not user:
            return self._json({"error": "unauthorized"}, 401)
        match = re.match(r"^/api/notes/([^/]+)$", urlparse(self.path).path)
        if not match:
            return self._json({"error": "not found"}, 404)
        try:
            data = self._read_body()
            note = write_note(
                user_vault(user),
                unquote(match.group(1)),
                data.get("title", "untitled"),
                data.get("body", ""),
                data.get("strokes", []),
                data.get("parent") or None,
                bool(data.get("pinned")),
            )
            self._json(note)
        except Exception as err:  # noqa: BLE001 - report any failure to the client
            self._json({"error": str(err)}, 500)

    def do_DELETE(self):
        user = self._current_user()
        if not user:
            return self._json({"error": "unauthorized"}, 401)
        path = urlparse(self.path).path

        if path == "/api/auth/account":
            data = self._read_body()
            password = data.get("password") or ""
            # re-check the password even though there's already a valid session — this is
            # an irreversible action (deletes every note), so it gets its own confirmation
            if not check_user_password(user, password):
                time.sleep(1)  # same brute-force slowdown as login
                return self._json({"error": "wrong password"}, 401)
            delete_user(user)
            return self._json({"ok": True}, headers=[self._clear_cookie_header()])

        match = re.match(r"^/api/trash/([^/]+)$", path)
        if match:
            # "delete forever" — removes from the trash, no way back after this
            purge_note(user_vault(user), unquote(match.group(1)))
            self.send_response(204)
            self.end_headers()
            return

        match = re.match(r"^/api/notes/([^/]+)$", path)
        if not match:
            return self._json({"error": "not found"}, 404)
        delete_note(user_vault(user), unquote(match.group(1)))
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
    os.makedirs(USERS_ROOT, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    ip = lan_ip()
    print("")
    print("  glyph notebook")
    print("  users : %s" % USERS_ROOT)
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
