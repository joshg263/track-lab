#!/usr/bin/env python3
"""Simple HTTP server for the viewer.

GET is unchanged: the viewer directory, plus /data/sites/ mapped onto
data/sites/.

GET /api/layouts/edits?site=<slug>
    JSON list of saved layout names in data/sites/<slug>/layouts/edits/.
    index.json is not itself a layout. The static viewer can fall back to
    that index when this endpoint is unavailable.

POST /api/layouts/edits
    JSON body in the layout schema. Writes
    data/sites/nishiwaki_basin/layouts/edits/<name>.json so layout_check.py
    can check it, and refreshes edits/index.json. Names are a single path
    segment; layouts A, B and C are rejected so those files cannot be replaced.

POST /api/session-events
    One event object, or a list. Appends JSON lines to logs/session-events.jsonl.
    Each event has ts (ISO-8601), layout_id, and type. Extra fields are kept.
    Known types: edit_mode_on, edit_mode_off, drag, add, delete, undo, reset,
    save, layout_switch, note.
"""

import http.server
import json
import os
import re
import socketserver
import sys
from urllib.parse import parse_qs, urlsplit

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(PROJECT_DIR, "viewer")
SITES_DIR = os.path.join(PROJECT_DIR, "data", "sites")
EDITS_DIR = os.path.join(SITES_DIR, "nishiwaki_basin", "layouts", "edits")
LOG_PATH = os.path.join(PROJECT_DIR, "logs", "session-events.jsonl")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TYPE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
RESERVED_NAMES = {
    "layout_a_straight_hairpin",
    "layout_b_elevation_sweep",
    "layout_c_arena",
    "check_metrics",
    # edits/index.json is the manifest, not a layout.
    "index",
}
MAX_BODY = 2_000_000
DEFAULT_SITE = "nishiwaki_basin"


def edits_directory(site):
    """Absolute edits directory for a site slug, or None if the slug is unsafe."""
    if not isinstance(site, str) or not NAME_RE.match(site):
        return None
    return os.path.join(SITES_DIR, site, "layouts", "edits")


def list_edit_names(directory):
    """Saved layout stems in directory, excluding the index and reserved names."""
    if not directory or not os.path.isdir(directory):
        return []
    names = []
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        name = fn[:-5]
        if NAME_RE.match(name) and name not in RESERVED_NAMES:
            names.append(name)
    return names


def write_edits_index(directory):
    """Refresh edits/index.json so a static server can list saved variants."""
    os.makedirs(directory, exist_ok=True)
    dest = os.path.join(directory, "index.json")
    tmp = dest + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"edits": list_edit_names(directory)}, f, indent=2)
        f.write("\n")
    os.replace(tmp, dest)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=VIEWER_DIR, **kwargs)

    def translate_path(self, path):
        path = urlsplit(path).path
        if path.startswith("/data/sites/"):
            return os.path.join(SITES_DIR, path[len("/data/sites/"):])
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/layouts/edits":
            site = (parse_qs(parsed.query).get("site") or [DEFAULT_SITE])[0]
            self._list_edits(site)
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._json(400, {"error": "bad length"})
            return
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "bad length"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "invalid json"})
            return
        if path == "/api/layouts/edits":
            self._save_layout(body)
        elif path == "/api/session-events":
            self._append_events(body)
        else:
            self._json(404, {"error": "not found"})

    def _save_layout(self, body):
        if not isinstance(body, dict):
            self._json(400, {"error": "expected object"})
            return
        name = body.get("name", "")
        if not isinstance(name, str) or not NAME_RE.match(name) or name in RESERVED_NAMES:
            self._json(400, {"error": "invalid name"})
            return
        points = body.get("control_points")
        if not isinstance(points, list) or not (3 <= len(points) <= 200):
            self._json(400, {"error": "control_points"})
            return
        clean_points = []
        for point in points:
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                self._json(400, {"error": "control point"})
                return
            try:
                item = {"x": float(point["x"]), "y": float(point["y"])}
            except (TypeError, ValueError):
                self._json(400, {"error": "control point"})
                return
            note = point.get("note")
            if isinstance(note, str) and note:
                item["note"] = note[:200]
            clean_points.append(item)
        try:
            width = float(body.get("track_width_m") or 14)
        except (TypeError, ValueError):
            width = 14
        payload = {
            "name": name,
            "direction": str(body.get("direction") or "")[:500],
            "version": 1,
            "track_width_m": width,
            "coordinate_system": "local_m",
            "control_points": clean_points,
            "notes": str(body.get("notes") or "")[:2000],
        }
        edited_from = body.get("edited_from")
        if isinstance(edited_from, str) and edited_from:
            payload["edited_from"] = edited_from[:80]
        os.makedirs(EDITS_DIR, exist_ok=True)
        dest = os.path.join(EDITS_DIR, name + ".json")
        if os.path.dirname(os.path.abspath(dest)) != os.path.abspath(EDITS_DIR):
            self._json(400, {"error": "invalid name"})
            return
        overwritten = os.path.exists(dest)
        tmp = dest + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, dest)
        write_edits_index(EDITS_DIR)
        rel = f"data/sites/nishiwaki_basin/layouts/edits/{name}.json"
        self._json(200, {"ok": True, "path": rel, "overwritten": overwritten})

    def _list_edits(self, site):
        directory = edits_directory(site)
        if directory is None:
            self._json(400, {"error": "invalid site"})
            return
        self._json(200, {"site": site, "edits": list_edit_names(directory)})

    def _append_events(self, body):
        events = body if isinstance(body, list) else [body]
        if not events or len(events) > 100:
            self._json(400, {"error": "events"})
            return
        clean = []
        for ev in events:
            if not isinstance(ev, dict):
                self._json(400, {"error": "event"})
                return
            if not isinstance(ev.get("ts"), str) or not isinstance(ev.get("type"), str):
                self._json(400, {"error": "event fields"})
                return
            if not TYPE_RE.match(ev["type"]):
                self._json(400, {"error": "event type"})
                return
            item = dict(ev)
            item["ts"] = ev["ts"][:40]
            item["type"] = ev["type"]
            item["layout_id"] = str(ev.get("layout_id") or "")[:80]
            clean.append(item)
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            for ev in clean:
                f.write(json.dumps(ev, separators=(",", ":")) + "\n")
        self._json(200, {"ok": True, "count": len(clean)})

    def _json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    if os.path.isdir(EDITS_DIR):
        write_edits_index(EDITS_DIR)
    with ThreadingServer(("", PORT), Handler) as httpd:
        print(f"Serving viewer at http://localhost:{PORT}")
        print(f"  Red Bull Ring: http://localhost:{PORT}/")
        print(f"  Japan sites:   http://localhost:{PORT}/site.html")
        print("Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
