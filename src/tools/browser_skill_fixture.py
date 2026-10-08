"""Exclusive daemon-origin, one-shot synthetic native form proof.

Not an application API, alternate browser driver, or general fixture framework.
The caller must be the exact daemon -m entry, before production configuration
imports. All controller state is disposable and no committing BSK IPC is exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextvars import ContextVar
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

URL = "http://127.0.0.1:18789/fixture/example-co/job/1"
_PATH = "/fixture/example-co/job/1"
PK = "example-co#synthetic-job"
COMPANY = "example-co"
_FACTS = {"Name": "Test User", "Additional question": "Synthetic answer"}
_FORM = ('''<!doctype html><html lang="en"><head><title>Synthetic application</title></head>
<body><h1>Synthetic role at example-co</h1>
<p>Offline fixture for a synthetic example-co job. No real person or employer.
Only the approved Name field may be written. This deliberately repetitive
synthetic job description helps the ordinary PAGE reader see a complete page.
This fixture does not ask for a real application, account, login or credentials.
Only a fake form is presented here; it is not associated with any live ATS.
Reviewing the page reveals exactly two native text controls and a native Submit.
The browser controller is expected to stop before any unapproved disclosure.
No external links, cross-origin resources or redirects are provided.</p>
<form id="synthetic-form" method="POST"
  action="http://127.0.0.1:18789/fixture/example-co/job/1">
<label for="name">Name</label><input id="name" name="name" type="text">
<label for="additional">Additional question</label>
<textarea id="additional" name="additional"></textarea>
<button id="submit" type="submit">Submit application</button>
</form></body></html>''').encode("utf-8")

# This context carries ONLY a one-shot object minted in run(). It is not a flag,
# user input, serialized token, request parameter or globally installed patch.
_SESSION_ENTRY: ContextVar[object | None] = ContextVar(
    "appliedin_private_native_fixture_entry", default=None)


def _daemon_origin() -> bool:
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return (getattr(spec, "name", None) == "daemon"
            and sys.argv[1:] == ["--synthetic-browser-fixture"]
            and os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
            and os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
            and os.environ.get("BSK_AUTO_START") == "0")


def _read_only_entry() -> bool:
    state = _SESSION_ENTRY.get()
    return type(state) is _SyntheticRun and state._origin is _ORIGIN and _daemon_origin()


def authorized(context: object, *, pk: str = "", company: str = "",
               url: str = "") -> bool:
    return (context is not None and context is _SESSION_ENTRY.get()
            and _read_only_entry()
            and (not pk or pk == PK)
            and (not company or company == COMPANY)
            and (not url or url == URL))


# Neither this private sentinel nor _SyntheticRun grants authority on its own:
# run() must create the instance AND bind the exact identity in _SESSION_ENTRY.
_ORIGIN = object()


class _SyntheticRun:
    __slots__ = ("_origin", "resume_path", "row", "events", "hold", "fills",
                 "navigations", "decisions", "status_writes")

    def __init__(self, resume_path: str):
        self._origin = _ORIGIN
        self.resume_path = resume_path
        self.row = {
            "pk": PK, "company": COMPANY, "status": "submitting",
            "gate_reason": "", "resume_seed": "fixture-base-seed",
            "resume_tex_key": "synthetic-only-resume",
            "human_approved_answers": {
                "Name": {"value": "Test User", "selector": "#name",
                         "label": "Name", "question": "", "url": URL},
            },
        }
        self.events = []
        self.hold = False
        self.fills = 0
        self.navigations = 0
        self.decisions = 0
        self.status_writes = []

    def matches(self, *, url: str, company: str, pk: str, facts: dict,
                model: str, resume_path: str) -> bool:
        return (authorized(self, pk=pk, company=company, url=url)
                and facts == _FACTS and model == "synthetic-fixture"
                and resume_path == self.resume_path)

    def duplicate_refusal(self) -> bool:
        return self.row.get("status") in {"applied", "applied_manual"}

    def blocked(self, pk: str, row: dict) -> bool:
        return (pk != PK or self.hold or row.get("possible_submission")
                or row.get("fail_kind") == "uncertain"
                or row.get("gate_reason") == "submit_uncertain")

    def seed_fingerprint(self) -> str:
        return "fixture-base-seed"

    def stage_resume(self, path: str, owner: str) -> str:
        if path != self.resume_path or not Path(path).is_file():
            raise ValueError("Synthetic attachment missing; correct fixture and rerun.")
        return path

    def site_rules(self, url: str, company: str) -> str:
        if (url, company) != (URL, COMPANY):
            raise ValueError("Synthetic fixture has no authority for this origin.")
        return ""

    def emit(self, kind: str, **details) -> None:
        self.events.append({"kind": kind, "pk": details.get("pk")})

    async def decision(self, task: str, page: dict, history: list, *, model: str):
        # The second fact exists. The *controller* must reject its missing exact
        # approval, not a fabricated model Gate or an earlier missing-fact Gate.
        actions = (
            {"action": "fill", "selector": "#name", "fact": "Name"},
            {"action": "fill", "selector": "#additional", "fact": "Additional question"},
        )
        if model != "synthetic-fixture" or self.decisions >= len(actions):
            raise ValueError("Synthetic decision sequence exhausted; rerun fixture.")
        action = actions[self.decisions]
        self.decisions += 1
        return action

    def mark_hold(self, pk: str) -> None:
        if pk != PK:
            raise ValueError("No synthetic hold for this job.")
        self.hold = True

    def is_held(self, pk: str) -> bool:
        return pk == PK and self.hold

    def set_status(self, pk: str, status, **attrs) -> None:
        if pk != PK:
            raise ValueError("No synthetic row for this job.")
        value = getattr(status, "value", status)
        self.row.update(status=value, **attrs)
        self.status_writes.append(value)

    def facade(self, raw):
        """A closed, tiny edge. The controller never receives the raw Session."""
        state = self

        class _Closed:
            __slots__ = ()

            async def navigate(self, url: str):
                if url != URL or state.navigations:
                    raise PermissionError("Fixture permits one navigate to the fixed job URL.")
                if not authorized(state, pk=PK, company=COMPANY, url=URL):
                    raise PermissionError("Fixture authority has expired.")
                state.navigations += 1
                return await raw.navigate(url)

            async def page(self):
                if not state.navigations or not authorized(state):
                    raise PermissionError("Fixture cannot inspect another session.")
                # The existing raw Session calls evaluate(PAGE), never model JS.
                return await raw.page()

            async def call(self, *args, **kwargs):
                if (args != ("fill", "#name", "--value", "Test User")
                        or kwargs or not state.navigations or state.fills
                        or not authorized(state, pk=PK, url=URL)):
                    # Every click (including Submit/Continue), upload, select,
                    # choose, evaluate/JS and second/wrong fill stops BEFORE IPC.
                    raise PermissionError("Fixture denies this browser operation.")
                state.fills += 1
                return await raw.call(*args)

        return _Closed()


class _FixtureHandler(BaseHTTPRequestHandler):
    def _reject(self, status: int = 404) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if (self.path != _PATH or self.headers.get("Host") != "127.0.0.1:18789"):
            self._reject()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(_FORM)))
        self.end_headers()
        self.wfile.write(_FORM)

    def do_POST(self) -> None:
        self._reject(405)

    do_HEAD = do_POST
    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
    do_OPTIONS = do_POST
    do_CONNECT = do_POST
    do_TRACE = do_POST

    def log_message(self, *args) -> None:
        pass  # Never log request headers, URLs or session data.


def run() -> int:
    """Only daemon's exact -m branch calls this, in a one-shot process."""
    if not _daemon_origin() or _SESSION_ENTRY.get() is not None:
        raise RuntimeError("Synthetic fixture requires the exclusive daemon CLI entry.")
    # Fail closed if the fixed port is occupied; NEVER choose another port,
    # host, profile or browser. No socket is started by import or offline tests.
    with HTTPServer(("127.0.0.1", 18789), _FixtureHandler) as server:
        server.timeout = 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory(prefix="appliedin-synthetic-fixture-") as root:
                document = Path(root) / "Synthetic-Resume.pdf"
                document.write_bytes(b"%PDF-1.4\n% synthetic fixture; never submitted\n%%EOF\n")
                state = _SyntheticRun(str(document))
                token = _SESSION_ENTRY.set(state)
                try:
                    # Imports only AFTER daemon sanitized its environment. The
                    # ordinary controller runs with its original F1-F5 checks.
                    from tools.browser_skill_apply import apply
                    result = asyncio.run(apply(
                        URL, COMPANY, dict(_FACTS), "synthetic-fixture", pk=PK,
                        resume_path=state.resume_path, fixture_context=state))
                finally:
                    _SESSION_ENTRY.reset(token)
                ok = (result.get("status") == "gate"
                      and (result.get("form_question") or {}).get("selector") == "#additional"
                      and state.fills == 1 and state.decisions == 2)
                print(json.dumps({"fixture": "native-form", "safe_gate": ok,
                                  "status": result.get("status")}))
                return 0 if ok else 1
        finally:
            server.shutdown()
            thread.join(timeout=3)
