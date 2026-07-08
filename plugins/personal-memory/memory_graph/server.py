from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .indexer import MemoryGraphIndexer


class MemoryGraphApp:
    def __init__(self, indexer: MemoryGraphIndexer, web_root: Path | None = None):
        self.indexer = indexer
        self.web_root = web_root or Path(__file__).parent / "web"
        self._lock = threading.Lock()
        self._watcher_started = False

    def api_graph(self, since: int | None = None) -> dict:
        with self._lock:
            return self.indexer.graph(since)

    def api_search(self, query: str, limit: int = 20) -> list[dict]:
        with self._lock:
            return self.indexer.search(query, limit)

    def api_node(self, node_id: str) -> dict:
        with self._lock:
            detail = self.indexer.node_detail(node_id)
        if detail is None:
            raise KeyError(node_id)
        return detail

    def api_events(self, since: int = 0) -> dict:
        graph = self.api_graph(since)
        return {"event_id": graph["event_id"], "changed": graph["event_id"] > since}

    def start_watcher(self, interval: float = 2.0) -> None:
        if self._watcher_started:
            return
        self._watcher_started = True

        def loop() -> None:
            while True:
                try:
                    changed = self.indexer.changed_paths()
                    if changed:
                        with self._lock:
                            self.indexer.rebuild("changed", changed[0])
                except Exception:
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    def make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                try:
                    if parsed.path == "/api/graph":
                        since = _int(query.get("since", ["0"])[0])
                        self._json(app.api_graph(since))
                    elif parsed.path == "/api/search":
                        self._json(app.api_search(query.get("q", [""])[0]))
                    elif parsed.path.startswith("/api/node/"):
                        self._json(app.api_node(unquote(parsed.path.removeprefix("/api/node/"))))
                    elif parsed.path == "/api/events":
                        self._json(app.api_events(_int(query.get("since", ["0"])[0])))
                    else:
                        self._static(parsed.path)
                except KeyError:
                    self._json({"error": "not found"}, status=404)

            def log_message(self, format: str, *args) -> None:
                return

            def _json(self, data, status: int = 200) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _static(self, path: str) -> None:
                rel = "index.html" if path in {"", "/"} else path.lstrip("/")
                target = (app.web_root / rel).resolve()
                try:
                    target.relative_to(app.web_root.resolve())
                except ValueError:
                    self.send_error(404)
                    return
                if not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("content-type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((host, port), self.make_handler())
        return server


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
