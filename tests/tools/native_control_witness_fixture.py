"""Synthetic, read-only HTML and GET-only loopback fixture for the manual witness smoke.

Only native_control_witness_smoke.py starts this server, inside a verified
private Linux network namespace. Importing this module never opens a socket.
"""
from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

PATH = "/fixture/native-control-witness"
CHECKBOX_CAPTION = "Confirm synthetic detail"
RADIO_CAPTIONS = ("Synthetic option Alpha", "Synthetic option Beta")
SELECT_OPTIONS = (
    ("first", "First synthetic selection"),
    ("second", "Second synthetic selection"),
    ("third", "Third synthetic selection"),
)

# The order is the actual DOM order, not a set: the witness must not omit,
# duplicate or silently reorder a native control.
CASES = (
    ("editable-input", "input", "text", True),
    ("editable-textarea", "textarea", "textarea", True),
    ("display-none-self", "input", "text", False),
    ("visibility-hidden-self", "input", "text", False),
    ("display-none-parent", "input", "text", False),
    ("visibility-hidden-parent", "input", "text", False),
    ("readonly-input", "input", "text", False),
    ("disabled-input", "input", "text", False),
    ("inert-input", "input", "text", False),
    ("inert-parent", "input", "text", False),
    ("zero-area", "input", "text", False),
    ("offcanvas", "input", "text", False),
    ("check-detail", "input", "checkbox", True),
    ("radio-alpha", "input", "radio", True),
    ("radio-beta", "input", "radio", True),
    ("selection", "select", "select", True),
    ("below-fold-scrollable", "input", "text", True),
)


def document(url: str) -> bytes:
    """Generate one fixed synthetic page; the only variable is its bound port."""
    action = escape(url, quote=True)
    markup = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Synthetic native witness matrix</title>
<link rel="icon" href="data:,">
<style>
html, body {{overflow-x:hidden;}}
body {{font:14px sans-serif; margin:16px;}}
.line {{display:block; margin:10px 0;}}
#display-none-self, #display-container {{display:none;}}
#visibility-hidden-self, #visibility-container {{visibility:hidden;}}
#zero-area {{box-sizing:border-box; width:0; height:0; min-width:0;
    min-height:0; padding:0; border:0; margin:0;}}
#offcanvas {{position:fixed; left:-6000px; top:40px;
    width:150px; height:25px;}}
#below-fold-row {{margin-top:1800px;}}
</style></head><body>
<h1>Synthetic native form only</h1>
<form id="witness-form" method="post" action="{action}">
<div class="line"><label for="editable-input">Synthetic name</label>
<input id="editable-input" type="text" name="synthetic_name"></div>
<div class="line"><label for="editable-textarea">Synthetic description</label>
<textarea id="editable-textarea" name="synthetic_description"></textarea></div>
<input id="display-none-self" style="display:none" type="text">
<input id="visibility-hidden-self" style="visibility:hidden" type="text">
<div id="display-container"><input id="display-none-parent" type="text"></div>
<div id="visibility-container"><input id="visibility-hidden-parent" type="text"></div>
<div class="line"><input id="readonly-input" type="text" readonly></div>
<div class="line"><input id="disabled-input" type="text" disabled></div>
<div class="line"><input id="inert-input" type="text" inert></div>
<div class="line" inert><input id="inert-parent" type="text"></div>
<div class="line"><input id="zero-area" type="text"></div>
<div class="line"><input id="offcanvas" type="text"></div>
<div class="line"><label for="check-detail">{escape(CHECKBOX_CAPTION)}</label>
<input id="check-detail" type="checkbox" name="synthetic_check"></div>
<fieldset><legend>Synthetic radio group</legend>
<label for="radio-alpha">{escape(RADIO_CAPTIONS[0])}</label>
<input id="radio-alpha" type="radio" name="synthetic_radio" value="alpha">
<label for="radio-beta">{escape(RADIO_CAPTIONS[1])}</label>
<input id="radio-beta" type="radio" name="synthetic_radio" value="beta">
</fieldset>
<div class="line"><label for="selection">Synthetic selection</label>
<select id="selection" name="synthetic_selection">'''
    markup += "".join(
        f'<option value="{escape(v, quote=True)}">{escape(label)}</option>'
        for v, label in SELECT_OPTIONS
    )
    markup += '''</select></div>
<div id="below-fold-row" class="line">
<label for="below-fold-scrollable">Synthetic scrollable field</label>
<input id="below-fold-scrollable" type="text"></div>
</form></body></html>'''
    return markup.encode("utf-8")


class Recorder:
    """Record every HTTP attempt reaching the fixture, including rejected ones."""

    def __init__(self):
        self._lock = Lock()
        self._requests: list[tuple[str, str, str, bool]] = []

    def record(self, method: str, path: str, host: str, accepted: bool):
        with self._lock:
            self._requests.append((method, path, host, accepted))

    def snapshot(self) -> list[tuple[str, str, str, bool]]:
        with self._lock:
            return list(self._requests)


def server() -> tuple[ThreadingHTTPServer, Recorder, str]:
    """Bind an ephemeral IPv4 loopback port, never an all-interface listener."""
    recorder = Recorder()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            host = self.headers.get("Host", "")
            correct = (self.command == "GET" and self.path == PATH
                       and host == f"127.0.0.1:{self.server.server_port}")
            recorder.record(self.command, self.path, host, correct)
            body = document(f"http://127.0.0.1:{self.server.server_port}{PATH}") if correct else b""
            self.send_response(200 if correct else 405)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "img-src data:; form-action 'none'")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            self._serve()

        def do_POST(self):
            self._serve()

        do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = do_POST

        def send_error(self, code, message=None, explain=None):
            # Even an unsupported HTTP method is an auditable rejected attempt.
            recorder.record(getattr(self, "command", "?"),
                            getattr(self, "path", "?"), self.headers.get("Host", ""), False)
            super().send_error(code, message, explain)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if httpd.server_address[0] != "127.0.0.1":
        httpd.server_close()
        raise RuntimeError("fixture is not bound to IPv4 loopback")
    url = f"http://127.0.0.1:{httpd.server_port}{PATH}"
    return httpd, recorder, url
