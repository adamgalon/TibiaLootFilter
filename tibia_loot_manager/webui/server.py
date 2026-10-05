"""Local-only HTTP server for the interface.

Security model: it listens on 127.0.0.1 only, every API call must carry the
random per-launch token (sent by the page, which receives it in its URL), and
the Host header must name this server, which stops DNS-rebinding pages from
reaching it. Static files hold no data, so they need no token.
"""

import json
import mimetypes
import secrets
import sys
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..i18n import _
from ..sources.http import SourceError
from .service import AppService, UserError

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")


def routes(svc: AppService) -> dict:
    """(method, path) → handler(params) where params merges the query string and the JSON body."""
    s = lambda v: str(v or "")  # noqa: E731
    b = lambda v: str(v).lower() in ("1", "true", "yes")  # noqa: E731
    return {
        ("GET", "state"): lambda p: svc.state(),
        ("POST", "theme"): lambda p: svc.set_theme(s(p.get("theme"))),
        ("POST", "notices/dismiss"): lambda p: svc.dismiss_notices(),
        ("GET", "onboarding"): lambda p: svc.onboarding(),
        ("POST", "onboarding/finish"): lambda p: svc.finish_onboarding(bool(p.get("start_full", True))),
        ("GET", "catalog"): lambda p: svc.catalog(s(p.get("q")), s(p.get("seg")) or "all", s(p.get("cat")),
                                                  s(p.get("idf")) or "all", int(p.get("offset") or 0),
                                                  int(p.get("limit") or 200)),
        ("GET", "item"): lambda p: svc.item(s(p.get("key"))),
        ("POST", "item/lookup"): lambda p: svc.lookup_drops(s(p.get("key"))),
        ("POST", "accepted/toggle"): lambda p: svc.toggle_accepted(s(p.get("key"))),
        ("POST", "accepted/bulk"): lambda p: svc.accepted_bulk(s(p.get("q")), s(p.get("seg")) or "all", s(p.get("cat")),
                                                       s(p.get("idf")) or "all", bool(p.get("add")),
                                                       bool(p.get("dry_run"))),
        ("GET", "strictness"): lambda p: svc.strictness_overview(),
        ("GET", "strictness/preview"): lambda p: svc.strictness_preview(s(p.get("level"))),
        ("POST", "strictness/apply"): lambda p: svc.strictness_apply(s(p.get("level"))),
        ("POST", "strictness/accept-changes"): lambda p: svc.strictness_accept_changes(),
        ("POST", "favorites/toggle"): lambda p: svc.toggle_favorite(s(p.get("key"))),
        ("POST", "searches/save"): lambda p: svc.save_search(s(p.get("name")), s(p.get("q")), s(p.get("seg")),
                                                     s(p.get("cat")), s(p.get("idf"))),
        ("POST", "searches/delete"): lambda p: svc.delete_search(s(p.get("name"))),
        ("POST", "accepted/follow"): lambda p: svc.set_follow_delivery(bool(p.get("on"))),
        ("POST", "accepted/defaults"): lambda p: svc.restore_accepted_defaults(),
        ("GET", "accepted"): lambda p: svc.accepted(s(p.get("tab")) or "active"),
        ("POST", "delivery/toggle"): lambda p: svc.toggle_delivery(s(p.get("key"))),
        ("POST", "delivery/defaults"): lambda p: svc.restore_delivery_defaults(),
        ("GET", "delivery"): lambda p: svc.delivery(s(p.get("tab")) or "active"),
        ("GET", "export"): lambda p: svc.export(s(p.get("sort")) or "name", b(p.get("ids"))),
        ("POST", "export/file"): lambda p: svc.export_file(),
        ("POST", "export/text"): lambda p: svc.export_text(s(p.get("sort")) or "name", bool(p.get("ids"))),
        ("GET", "install"): lambda p: svc.install(p.get("selected")),
        ("POST", "install/folder"): lambda p: svc.set_folder(bool(p.get("default"))),
        ("POST", "install/label"): lambda p: svc.set_label(s(p.get("folder")), s(p.get("label"))),
        ("POST", "install/preview"): lambda p: svc.install_preview(s(p.get("folder")), s(p.get("mode"))),
        ("POST", "install/apply"): lambda p: svc.install_apply(s(p.get("folder")), s(p.get("mode"))),
        ("POST", "restore/preview"): lambda p: svc.restore_preview(s(p.get("folder")), s(p.get("file"))),
        ("POST", "restore/apply"): lambda p: svc.restore_apply(s(p.get("folder")), s(p.get("file"))),
        ("POST", "open-folder"): lambda p: svc.open_folder(s(p.get("which")), p.get("folder")),
        ("GET", "sources"): lambda p: svc.sources(),
        ("POST", "settings/limit"): lambda p: svc.set_limit(p.get("limit")),
        ("POST", "update/start"): lambda p: svc.start_update(),
        ("GET", "update/status"): lambda p: svc.update_status(),
        ("POST", "update/apply"): lambda p: svc.apply_update(bool(p.get("keep_removed"))),
        ("POST", "update/cancel"): lambda p: svc.cancel_update(),
        ("GET", "help"): lambda p: svc.help(),
        ("GET", "weekly"): lambda p: svc.weekly_overview(),
        ("GET", "weekly/search"): lambda p: svc.weekly_search(s(p.get("q"))),
        ("POST", "weekly/add"): lambda p: svc.weekly_add(s(p.get("key")), p.get("required")),
        ("POST", "weekly/set"): lambda p: svc.weekly_set(s(p.get("key")), p.get("required"), p.get("collected"),
                                                         p.get("delta")),
        ("POST", "weekly/remove"): lambda p: svc.weekly_remove(s(p.get("key"))),
        ("POST", "weekly/add-missing"): lambda p: svc.weekly_add_missing_to_accepted(),
        ("POST", "hunts/analyze"): lambda p: svc.hunt_analyze(s(p.get("text"))),
        ("GET", "hunts"): lambda p: svc.hunt_list(),
        ("GET", "hunts/open"): lambda p: svc.hunt_open(s(p.get("id"))),
        ("POST", "hunts/delete"): lambda p: svc.hunt_delete(s(p.get("id"))),
        ("POST", "hunts/apply-tasks"): lambda p: svc.hunt_apply_tasks(s(p.get("id"))),
        ("GET", "profiles"): lambda p: svc.profile_list(),
        ("POST", "profiles/create"): lambda p: svc.profile_create(s(p.get("name")), s(p.get("start")) or "delivery"),
        ("POST", "profiles/switch"): lambda p: svc.profile_switch(s(p.get("id"))),
        ("POST", "profiles/rename"): lambda p: svc.profile_rename(s(p.get("id")), s(p.get("name"))),
        ("POST", "profiles/duplicate"): lambda p: svc.profile_duplicate(s(p.get("id"))),
        ("POST", "profiles/delete"): lambda p: svc.profile_delete(s(p.get("id"))),
        ("GET", "profiles/history"): lambda p: svc.profile_history(s(p.get("id"))),
        ("POST", "profiles/restore"): lambda p: svc.profile_restore(s(p.get("id")), int(p.get("index"))),
        ("GET", "profiles/compare"): lambda p: svc.profile_compare(s(p.get("a")), s(p.get("b"))),
        ("POST", "profiles/export"): lambda p: svc.profile_export(s(p.get("id"))),
        ("POST", "profiles/import"): lambda p: svc.profile_import(),
        ("POST", "profiles/from-character"): lambda p: svc.profile_from_character(s(p.get("folder"))),
        ("GET", "report/template"): lambda p: svc.report_template(s(p.get("category")) or "bug", p.get("key"),
                                                                  p.get("operation"), p.get("error")),
        ("POST", "report/compose"): lambda p: svc.report_compose(s(p.get("category")), s(p.get("title")),
                                                                 p.get("values") or {}, p.get("diagnostics")),
        ("POST", "report/links"): lambda p: svc.report_links(s(p.get("title")), s(p.get("body"))),
        ("POST", "report/save"): lambda p: svc.report_save(s(p.get("title")), s(p.get("body")),
                                                           p.get("choose", True) is not False),
        ("POST", "open-url"): lambda p: svc.open_url(s(p.get("url"))),
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: AppService, port: int = 0, on_ping=None):
        self.service = service
        self.token = secrets.token_urlsafe(24)
        self.routes = routes(service)
        self.on_ping = on_ping or (lambda: None)
        super().__init__(("127.0.0.1", port), Handler)

    def handle_error(self, request, client_address):
        """A window closing drops its connections; that is normal, not an error worth a traceback."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        extra = extra or {}
        if "Cache-Control" not in extra:
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _read_body(self) -> bytes | None:
        """Always consume the request body: on a keep-alive connection unread bytes
        would otherwise be parsed as the start of the next request."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def _dispatch(self, method: str) -> None:
        self._body = self._read_body() if method == "POST" else b""
        if self._body is None:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
        if not self._host_ok():
            self.close_connection = True
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith("/api/"):
            return self._api(method, url)
        if url.path.startswith("/sprite/") and method == "GET":
            return self._sprite(url)
        if method != "GET":
            return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
        return self._static(url.path)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else urllib.parse.unquote(path.lstrip("/"))
        target = (WEB_ROOT / rel).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        csp = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype, {"Content-Security-Policy": csp})

    def _sprite(self, url) -> None:
        """Item image. <img> can't send headers, so the token comes in the query string."""
        query = urllib.parse.parse_qs(url.query)
        if not secrets.compare_digest((query.get("t") or [""])[-1], self.server.token):
            self.close_connection = True
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
        name = url.path[len("/sprite/"):]
        if not name.isdigit() or len(name) > 9:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        data = self.server.service.item_image(int(name))
        if not data:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no image"})
        # The URL carries the client version, so a cached image is never stale.
        self._send(HTTPStatus.OK, data, "image/png", {"Cache-Control": "private, max-age=604800, immutable"})

    def _api(self, method: str, url) -> None:
        if not secrets.compare_digest(self.headers.get("X-Token", ""), self.server.token):
            self.close_connection = True
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
        name = url.path[len("/api/"):]
        if name == "ping":
            self.server.on_ping()
            return self._json(HTTPStatus.OK, {})
        handler = self.server.routes.get((method, name))
        if handler is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown endpoint {name}"})
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        if method == "POST":
            try:
                body = json.loads(self._body or b"{}")
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            if isinstance(body, dict):
                params.update(body)
        try:
            return self._json(HTTPStatus.OK, handler(params))
        except UserError as e:
            return self._json(HTTPStatus.CONFLICT, {"error": str(e), "user": True})
        except (ValueError, TypeError) as e:  # malformed parameters
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid request: {e}", "user": True})
        except SourceError as e:
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(e), "user": True})
        except Exception as e:  # unexpected: report it so the page can offer a problem report
            traceback.print_exc()
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                              {"error": _("Unexpected error: {error}").format(error=e), "user": False})
