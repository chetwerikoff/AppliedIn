"""Daemon-exclusive synthetic application form and read-only safety proof.

No alternative browser driver or general fixture framework. The caller must be
the exact daemon -m entry before production configuration imports. No committing
BSK IPC is permitted; only the fixed form GET is served and every form method
is refused with 405.
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
_RESUME_BYTES = (b"%PDF-1.4" + bytes([10])
                 + b"% synthetic fixture; never submitted" + bytes([10])
                 + b"%%EOF" + bytes([10]))
COMPANY = "example-co"
_FACTS = {
    "Name": "Test User",
    "Additional question": "Synthetic answer",
    "Where are you located?": "Synthetic Example City",
    "What is your phone country?": "Example Country",
    "Are you authorized to work?": "Yes",
}
_MACHINE_VALUES = {
    "Where are you located?": "SYNTHETIC_CITY",
    "What is your phone country?": "EX",
    "Are you authorized to work?": "yes",
}
_OPTION_SELECTORS = {
    "#location-option-city": "Where are you located?",
    "#phone-country-option-example": "What is your phone country?",
    "#work-authorization-option-yes": "Are you authorized to work?",
}
_COMBO_SELECTORS = {
    "Where are you located?": "#location",
    "What is your phone country?": "#phone-country",
    "Are you authorized to work?": "#work-authorization",
}
_HIDDEN_SELECTORS = {
    "Where are you located?": "#location-code",
    "What is your phone country?": "#phone-country-code",
    "Are you authorized to work?": "#work-authorization-code",
}
_FORM = ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Synthetic application</title>
<style>[role="listbox"][hidden],[role="option"][hidden]{display:none}</style>
</head><body><h1>Synthetic role at example-co</h1>
<p>Offline fixture for synthetic example-co facts only. No real person or employer.
This form is served only by the daemon's --synthetic-browser-fixture mode.
It has no external links, cross-origin resources, redirects or real ATS connection.</p>
<form id="synthetic-form" method="POST" enctype="multipart/form-data"
 action="http://127.0.0.1:18789/fixture/example-co/job/1">
<div class="form-group"><label for="name" data-automation-id="formLabel">Name</label>
<input id="name" name="name" type="text"></div>
<div class="form-group"><label for="resume">Résumé</label>
<input id="resume" name="resume" type="file" required></div>
<div class="form-group"><div id="location-question" data-automation-id="formLabel">
Where are you located?</div>
<input id="location" name="location" type="text" role="combobox"
 aria-labelledby="location-question" aria-controls="location-options"
 aria-autocomplete="list" aria-expanded="false" aria-required="true"
 data-value-input="location-code">
<input id="location-code" name="location_value" type="hidden"></div>
<div id="location-options" role="listbox" hidden>
<div id="location-option-city" role="option" data-value="SYNTHETIC_CITY"
 aria-selected="false">Synthetic Example City</div>
<div id="location-option-other" role="option" data-value="OTHER_CITY"
 aria-selected="false">Other Synthetic City</div></div>
<div class="form-group"><div id="phone-country-question" data-automation-id="formLabel">
What is your phone country?</div>
<input id="phone-country" name="phone_country" type="text" role="combobox"
 aria-labelledby="phone-country-question" aria-controls="phone-country-options"
 aria-autocomplete="list" aria-expanded="false" aria-required="true"
 data-value-input="phone-country-code">
<input id="phone-country-code" name="phone_country_value" type="hidden"></div>
<div id="phone-country-options" role="listbox" hidden>
<div id="phone-country-option-example" role="option" data-value="EX"
 aria-selected="false">Example Country</div>
<div id="phone-country-option-other" role="option" data-value="OT"
 aria-selected="false">Other Synthetic Country</div></div>
<div class="form-group"><div id="work-authorization-question" data-automation-id="formLabel">
Are you authorized to work?</div>
<div id="work-authorization" role="combobox" tabindex="0"
 aria-labelledby="work-authorization-question"
 aria-controls="work-authorization-options" aria-expanded="false"
 aria-required="true" data-value-input="work-authorization-code">Choose an answer</div>
<input id="work-authorization-code" name="work_authorization_value" type="hidden">
</div>
<div id="work-authorization-options" role="listbox" hidden>
<div id="work-authorization-option-yes" role="option" data-value="yes"
 aria-selected="false">Yes</div>
<div id="work-authorization-option-no" role="option" data-value="no"
 aria-selected="false">No</div></div>
<div class="form-group"><label for="additional" data-automation-id="formLabel">Additional question</label>
<textarea id="additional" name="additional"></textarea></div>
<button id="submit" type="submit">Submit application</button></form>
<script>
function bindPicker(comboId,listId,asyncFilter){
 const combo=document.getElementById(comboId), list=document.getElementById(listId);
 const options=Array.from(list.querySelectorAll('[role="option"]'));
 const show=()=>{list.hidden=false;combo.setAttribute('aria-expanded','true');};
 combo.addEventListener('click',show);
 if(combo.tagName==='INPUT'){
  combo.addEventListener('focus',show);
  combo.addEventListener('input',()=>{
   list.hidden=true;
   const update=()=>{
    const needle=combo.value.trim().toLowerCase();
    options.forEach(o=>{o.hidden=!!needle&&!o.innerText.toLowerCase().includes(needle)});
    show();
   };
   if(asyncFilter)window.setTimeout(update,60);else update();
  });
 }
 options.forEach(option=>option.addEventListener('click',()=>{
  const caption=option.innerText.trim(), value=option.getAttribute('data-value');
  if(combo.tagName==='INPUT')combo.value=caption;else combo.innerText=caption;
  combo.setAttribute('aria-valuetext',caption);
  combo.setAttribute('data-selected-label',caption);
  combo.setAttribute('data-selected-value',value);
  options.forEach(o=>o.setAttribute('aria-selected',String(o===option)));
  document.getElementById(combo.getAttribute('data-value-input')).value=value;
  list.hidden=true;combo.setAttribute('aria-expanded','false');
 }));
}
bindPicker('location','location-options',true);
bindPicker('phone-country','phone-country-options',false);
bindPicker('work-authorization','work-authorization-options',false);
</script></body></html>''').encode("utf-8")


def _inventory_diagnostic_script() -> str:
    # Instrument the fixed observer only for reporting. Its original result is
    # still the sole inventory passed to the controller and all its guards.
    from tools.browser_skill_dom import PAGE
    script = PAGE.replace(
        "const opaqueControls = Array.from(document.querySelectorAll('*')).some(e =>",
        "const opaqueElements = Array.from(document.querySelectorAll('*')).filter(e =>")
    script = script.replace(
        "  const inventoryVerified =",
        "  const opaqueControls = opaqueElements.length > 0;\n  const inventoryVerified =")
    return script.replace(
        "return {url:location.href, title, text:text.slice(0,100000),",
        "return {ready_state:document.readyState, "
        "offending_elements:opaqueElements.map(e=>({selector:selector(e),"
        "tag:e.tagName.toLowerCase(),role:e.getAttribute('role')})), "
        "shadow_hosts:shadowRoots.map(e=>({selector:selector(e),"
        "tag:e.tagName.toLowerCase()})), "
        "url:location.href, title, text:text.slice(0,100000),")


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
                 "navigations", "decisions", "status_writes", "positive",
                 "ipc_index", "open_controls", "input_values", "committed",
                 "post_count", "post_payload", "submit_ipc", "uploaded",
                 "last_page", "combo_observations", "combo_touched",
                 "submit_call_entered", "submit_guard", "post_attempts",
                 "post_rejection", "post_receipts", "inventory_diagnostics")
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
        self.positive = False
        self.ipc_index = 0
        self.open_controls = set()
        self.input_values = {}
        self.committed = {}
        self.post_count = 0
        self.post_payload = None
        self.submit_ipc = 0
        self.uploaded = False
        self.last_page = None
        self.combo_observations = {}
        self.combo_touched = set()
        self.submit_call_entered = 0
        self.submit_guard = "not_reached"
        self.post_attempts = 0
        self.post_rejection = ""
        self.post_receipts = []
        self.inventory_diagnostics = None


    def enable_positive_fixture(self) -> None:
        self.positive = True
        specs = {
            "Name": ("#name", "Name", "Name", "input", "text"),
            "Where are you located?": ("#location", "Where are you located?",
                                        "Where are you located?", "input", "combobox"),
            "What is your phone country?": ("#phone-country",
                "What is your phone country?", "What is your phone country?",
                "input", "combobox"),
            "Are you authorized to work?": ("#work-authorization",
                "Are you authorized to work?", "Are you authorized to work?",
                "div", "combobox"),
        }
        for key, (selector, label, question, tag, typ) in specs.items():
            self.row["human_approved_answers"][key] = {
                "value": _FACTS[key], "selector": selector, "label": label,
                "question": question, "url": URL, "tag": tag, "type": typ,
                "form_selector": "#synthetic-form", "form_action": URL,
            }

    def _positive_ipc(self) -> tuple[tuple[str, ...], ...]:
        return (
            ("fill", "#name", "--value", _FACTS["Name"]),
            ("click", "#location"),
            ("fill", "#location", "--value", _FACTS["Where are you located?"]),
            ("click", "#location-option-city"),
            ("click", "#phone-country"),
            ("fill", "#phone-country", "--value", _FACTS["What is your phone country?"]),
            ("click", "#phone-country-option-example"),
            ("click", "#work-authorization"),
            ("click", "#work-authorization-option-yes"),
        )

    def observe_page(self, observed: dict) -> None:
        """Retain browser-read caption/value pairs as committed fixture state."""
        self.last_page = observed
        self.committed = {}
        self.combo_observations = {}
        controls = observed.get("controls", []) if isinstance(observed, dict) else []
        for key, selector in _COMBO_SELECTORS.items():
            if key not in self.combo_touched:
                self.combo_observations[key] = {
                    "outcome": "not_reached",
                    "visible_text": "", "selected_value": "", "hidden_value": "",
                    "reason": "combobox interaction was not reached",
                }
                continue
            matches = [c for c in controls
                       if isinstance(c, dict) and c.get("selector") == selector]
            if len(matches) != 1:
                self.combo_observations[key] = {
                    "outcome": "not_committed",
                    "visible_text": "", "selected_value": "", "hidden_value": "",
                    "reason": f"expected one observed control, found {len(matches)}",
                }
                continue
            combo = matches[0].get("combobox") or {}
            visible = combo.get("selected_text") or ""
            selected = combo.get("selected_value") or ""
            hidden_selector = combo.get("hidden_selector") or ""
            hidden = combo.get("hidden_value") or ""
            reasons = []
            if visible != _FACTS[key]:
                reasons.append("visible caption did not match the approved answer")
            if selected != _MACHINE_VALUES[key]:
                reasons.append("selected machine value did not match the expected value")
            if hidden_selector != _HIDDEN_SELECTORS[key]:
                reasons.append("linked hidden-value control was missing or changed")
            if hidden != _MACHINE_VALUES[key]:
                reasons.append("hidden machine value did not match the selected value")
            observation = {
                "outcome": "committed" if not reasons else "not_committed",
                "visible_text": visible, "selected_value": selected,
                "hidden_value": hidden,
                "reason": "; ".join(reasons),
            }
            self.combo_observations[key] = observation
            if not reasons:
                self.committed[key] = (_FACTS[key], _MACHINE_VALUES[key])


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
        actions = (
            {"action": "fill", "selector": "#name", "fact": "Name"},
            {"action": "combobox", "selector": "#location",
             "fact": "Where are you located?"},
            {"action": "combobox", "selector": "#phone-country",
             "fact": "What is your phone country?"},
            {"action": "combobox", "selector": "#work-authorization",
             "fact": "Are you authorized to work?"},
            {"action": "fill", "selector": "#additional",
             "fact": "Additional question"},
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
        """A closed edge exposing only the original controller's exact fixture path."""
        state = self
        async def diagnose(observed):
            if (isinstance(observed, dict) and (
                    observed.get("inventory_verified") is not True
                    or observed.get("truncated") or observed.get("opaque_controls")
                    or observed.get("shadow_roots") or observed.get("unsupported_frames"))):
                result = await raw.call("evaluate", _inventory_diagnostic_script())
                state.inventory_diagnostics = result.get("value")


        class _Closed:
            __slots__ = ()

            async def navigate(self, url: str):
                if url != URL or state.navigations:
                    raise PermissionError("Fixture permits one navigate to the fixed job URL.")
                if not authorized(state, pk=PK, company=COMPANY, url=URL):
                    raise PermissionError("Fixture authority has expired.")
                state.navigations += 1
                observed = await raw.navigate(url)
                state.observe_page(observed)
                if not isinstance(observed, dict) or observed.get("url") != URL:
                    raise PermissionError("Fixture redirected outside the fixed job URL.")
                await diagnose(observed)
                return observed

            async def page(self):
                if not state.navigations or not authorized(state):
                    raise PermissionError("Fixture cannot inspect another session.")
                observed = await raw.page()
                state.observe_page(observed)
                if not isinstance(observed, dict) or observed.get("url") != URL:
                    raise PermissionError("Fixture page escaped the fixed job URL.")
                await diagnose(observed)
                return observed

            async def call(self, *args, **kwargs):
                if not state.navigations or not authorized(state, pk=PK, url=URL):
                    raise PermissionError("Fixture denies this browser operation.")
                if state.positive and args and args[0] == "evaluate":
                    from tools.browser_skill_apply import _FIXED_CONTROL_WITNESS
                    if args != ("evaluate", _FIXED_CONTROL_WITNESS) or kwargs:
                        raise PermissionError("Fixture denies this browser operation.")
                    return await raw.call(*args)
                if not state.positive:
                    if (args != ("fill", "#name", "--value", "Test User")
                            or kwargs or state.fills):
                        raise PermissionError("Fixture denies this browser operation.")
                    state.fills += 1
                    return await raw.call(*args)
                if args == ("click", "#submit"):
                    state.submit_call_entered += 1
                    state.submit_guard = "denied: fixture forbids committing BSK IPC"
                    raise PermissionError("Fixture never permits submission-capable IPC.")
                sequence = state._positive_ipc()
                if (state.ipc_index >= len(sequence) or args != sequence[state.ipc_index]
                        or (args[0] == "upload" and kwargs != {"timeout": 130})
                        or (args[0] != "upload" and kwargs)):
                    raise PermissionError("Fixture denies this browser operation.")
                if args[0] == "upload" and (
                        args[3] != state.resume_path
                        or Path(state.resume_path).read_bytes() != _RESUME_BYTES):
                    raise PermissionError("Fixture attachment is not the staged synthetic PDF.")
                if args[0] == "click" and args[1] in _OPTION_SELECTORS:
                    key = _OPTION_SELECTORS[args[1]]
                    selector = {
                        "Where are you located?": "#location",
                        "What is your phone country?": "#phone-country",
                        "Are you authorized to work?": "#work-authorization",
                    }[key]
                    if selector not in state.open_controls:
                        raise PermissionError("Fixture option was not opened by its combobox.")
                    if selector != "#work-authorization" and state.input_values.get(selector) != _FACTS[key]:
                        raise PermissionError("Fixture filter did not equal its approved fact.")
                await raw.call(*args, **kwargs)
                for key, selector in _COMBO_SELECTORS.items():
                    if (args[0] in {"click", "fill"} and len(args) > 1
                            and (args[1] == selector
                                 or _OPTION_SELECTORS.get(args[1]) == key)):
                        state.combo_touched.add(key)
                state.ipc_index += 1
                if args[0] == "fill":
                    state.fills += 1
                    state.input_values[args[1]] = args[3]
                elif args[0] == "upload":
                    state.uploaded = True
                elif args[0] == "click" and args[1] in {
                        "#location", "#phone-country", "#work-authorization"}:
                    state.open_controls.add(args[1])
                elif args[0] == "click" and args[1] in _OPTION_SELECTORS:
                    key = _OPTION_SELECTORS[args[1]]
                    selector = {
                        "Where are you located?": "#location",
                        "What is your phone country?": "#phone-country",
                        "Are you authorized to work?": "#work-authorization",
                    }[key]
                    state.open_controls.discard(selector)

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
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(_FORM)))
        self.end_headers()
        self.wfile.write(_FORM)

    def _record_post_receipt(self, state, status: int) -> None:
        if type(state) is _SyntheticRun:
            state.post_receipts.append({
                "request_line": getattr(self, "requestline", f"POST {self.path}"),
                "host": self.headers.get("Host", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "response_status": status,
            })

    def do_POST(self) -> None:
        state = getattr(getattr(self, "server", None), "synthetic_run", None)
        if type(state) is _SyntheticRun:
            state.post_attempts += 1
            state.post_rejection = "Synthetic fixture refuses every non-GET form method."
        self._record_post_receipt(state, 405)
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
                document.write_bytes(_RESUME_BYTES)
                state = _SyntheticRun(str(document))
                state.enable_positive_fixture()
                server.synthetic_run = state
                token = _SESSION_ENTRY.set(state)
                try:
                    # Imports only AFTER daemon sanitized its environment. The
                    # ordinary controller runs with its original F1-F5 checks.
                    from tools.browser_skill_apply import (
                        apply, _approval_question_collisions)
                    result = asyncio.run(apply(
                        URL, COMPANY, dict(_FACTS), "synthetic-fixture", pk=PK,
                        resume_path=state.resume_path, fixture_context=state))
                finally:
                    _SESSION_ENTRY.reset(token)
                form_question = result.get("form_question") or {}
                expected_commits = {key: (_FACTS[key], value)
                                     for key, value in _MACHINE_VALUES.items()}
                # Name plus the two filtered pickers are the only fills; the
                # fourth picker uses clicks, then the fifth decision gates.
                expected_fills = 3
                expected_decisions = 5
                ok = (result.get("status") == "gate"
                      and form_question.get("selector") == "#additional"
                      and state.fills == expected_fills
                      and state.decisions == expected_decisions
                      and state.committed == expected_commits
                      and all(state.combo_observations.get(key, {}).get("outcome")
                              == "committed"
                              and state.combo_observations[key].get("visible_text")
                              == _FACTS[key]
                              and state.combo_observations[key].get("selected_value")
                              == _MACHINE_VALUES[key]
                              and state.combo_observations[key].get("hidden_value")
                              == _MACHINE_VALUES[key]
                              for key in _MACHINE_VALUES)
                      and not state.uploaded
                      and state.submit_call_entered == 0
                      and state.submit_guard == "not_reached"
                      and state.submit_ipc == 0
                      and state.post_attempts == 0
                      and state.post_count == 0)
                field_selectors = {"Name": "#name",
                                   "Additional question": "#additional"}
                outcomes = [
                    {"field": key, "outcome": (
                        "filled" if state.input_values.get(selector) == _FACTS[key]
                        else "not_reached")}
                    for key, selector in field_selectors.items()
                ]
                for key in _MACHINE_VALUES:
                    observation = state.combo_observations.get(key, {
                        "outcome": "not_reached",
                        "visible_text": "", "selected_value": "",
                        "hidden_value": "",
                        "reason": "no BrowserSkill interaction reached this combobox",
                    })
                    outcomes.append({"field": key, **observation})
                outcomes.append({
                    "field": "Résumé",
                    "outcome": "uploaded" if state.uploaded else "not_reached",
                })
                collisions = []
                seen_collision_sets = set()
                if isinstance(state.last_page, dict):
                    for target in state.last_page.get("controls", []):
                        if (not isinstance(target, dict)
                                or target.get("type") in {"radio", "hidden"}):
                            continue
                        question = target.get("question") or target.get("label")
                        if not isinstance(question, str) or not question.strip():
                            continue
                        others = [
                            c for c in _approval_question_collisions(
                                state.last_page, target)
                            if c.get("type") != "hidden"]
                        pair = [target, *others]
                        selectors = tuple(sorted(
                            c.get("selector", "") for c in pair))
                        if len(pair) < 2 or selectors in seen_collision_sets:
                            continue
                        seen_collision_sets.add(selectors)
                        collisions.append({
                            "question": question,
                            "controls": [{
                                "selector": c.get("selector"),
                                "label": c.get("label"),
                                "type": c.get("type"),
                            } for c in pair],
                        })
                gate = None if result.get("status") == "applied" else {
                    "kind": result.get("reason") or result.get("status"),
                    "field": {selector: field for field, selector in (
                        *field_selectors.items(),
                        ("Where are you located?", "#location"),
                        ("What is your phone country?", "#phone-country"),
                        ("Are you authorized to work?", "#work-authorization"),
                    )}.get(form_question.get("selector")),
                    "reason": result.get("question") or result.get("detail"),
                }
                inventory = {key: (state.last_page or {}).get(key) for key in (
                    "inventory_verified", "truncated", "opaque_controls",
                    "shadow_roots", "unsupported_frames")}
                if isinstance(state.inventory_diagnostics, dict):
                    inventory["diagnostics"] = {
                        key: state.inventory_diagnostics.get(key) for key in (
                            "ready_state", "offending_elements", "shadow_hosts")}
                print(json.dumps({"fixture": "native-form",
                                  "safe_gate": ok,
                                  "submitted": state.submit_ipc > 0,
                                  "status": result.get("status"),
                                  "comboboxes": len(state.committed),
                                  "post_received": state.post_count == 1,
                                  "gate": gate, "fields": outcomes,
                                  "approval_question_collisions": collisions,
                                  "inventory": inventory,
                                  "submit": {
                                      "guard": state.submit_guard,
                                      "call_entered": state.submit_call_entered > 0,
                                      "ipc_returned": state.submit_ipc > 0,
                                      "post_attempts": state.post_attempts,
                                      "post_rejection": state.post_rejection,
                                      "receipts": state.post_receipts,
                                  }}))
                return 0 if ok else 1
        finally:
            server.shutdown()
            thread.join(timeout=3)
