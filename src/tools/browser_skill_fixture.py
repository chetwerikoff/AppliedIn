"""Daemon-exclusive synthetic application form and loopback submission proof.

No alternative browser driver or general fixture framework. The caller must be
the exact daemon -m entry before production configuration imports. Its closed
controller edge permits only fixed synthetic fields and one validated loopback POST.
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
_POST_FIELDS = {
    "name": _FACTS["Name"],
    "additional": _FACTS["Additional question"],
    "location": _FACTS["Where are you located?"],
    "location_value": _MACHINE_VALUES["Where are you located?"],
    "phone_country": _FACTS["What is your phone country?"],
    "phone_country_value": _MACHINE_VALUES["What is your phone country?"],
    "work_authorization": _FACTS["Are you authorized to work?"],
    "work_authorization_value": _MACHINE_VALUES["Are you authorized to work?"],
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
<div class="form-group"><label for="additional" data-automation-id="formLabel">Additional question</label>
<textarea id="additional" name="additional"></textarea></div>
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
 aria-required="true" data-value-input="work-authorization-code"
 data-label-input="work-authorization-caption">Choose an answer</div>
<input id="work-authorization-code" name="work_authorization_value" type="hidden">
<input id="work-authorization-caption" name="work_authorization" type="hidden">
</div>
<div id="work-authorization-options" role="listbox" hidden>
<div id="work-authorization-option-yes" role="option" data-value="yes"
 aria-selected="false">Yes</div>
<div id="work-authorization-option-no" role="option" data-value="no"
 aria-selected="false">No</div></div>
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
  const labelInput=combo.getAttribute('data-label-input');
  if(labelInput)document.getElementById(labelInput).value=caption;
  list.hidden=true;combo.setAttribute('aria-expanded','false');
 }));
}
bindPicker('location','location-options',true);
bindPicker('phone-country','phone-country-options',false);
bindPicker('work-authorization','work-authorization-options',false);
</script></body></html>''').encode("utf-8")

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
                 "last_page", "combo_observations", "submit_call_entered",
                 "submit_guard", "post_attempts", "post_rejection", "post_receipts")
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
        self.submit_call_entered = 0
        self.submit_guard = "not_reached"
        self.post_attempts = 0
        self.post_rejection = ""
        self.post_receipts = []


    def enable_positive_fixture(self) -> None:
        self.positive = True
        specs = {
            "Name": ("#name", "Name", "Name", "input", "text"),
            "Additional question": ("#additional", "Additional question",
                                    "Additional question", "textarea", "textarea"),
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
            ("fill", "#additional", "--value", _FACTS["Additional question"]),
            ("click", "#location"),
            ("fill", "#location", "--value", _FACTS["Where are you located?"]),
            ("click", "#location-option-city"),
            ("click", "#phone-country"),
            ("fill", "#phone-country", "--value", _FACTS["What is your phone country?"]),
            ("click", "#phone-country-option-example"),
            ("click", "#work-authorization"),
            ("click", "#work-authorization-option-yes"),
            ("upload", "#resume", "--file", self.resume_path),
            ("click", "#submit"),
        )

    def observe_page(self, observed: dict) -> None:
        """Retain browser-read caption/value pairs as committed fixture state."""
        self.last_page = observed
        self.committed = {}
        self.combo_observations = {}
        controls = observed.get("controls", []) if isinstance(observed, dict) else []
        for key, selector in _COMBO_SELECTORS.items():
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

    def record_post(self, payload: dict[str, str], *, resume_name: str,
                    resume_bytes: bytes) -> bool:
        checks = (
            (self.positive and self._origin is _ORIGIN and _daemon_origin(),
             "fixture authority or positive run state was invalid"),
            (self.uploaded, "synthetic résumé upload was not verified"),
            (not self.post_count, "a duplicate form POST was rejected"),
            (payload == _POST_FIELDS,
             "submitted field names or values did not match the expected form"),
            (self.committed == {key: (_FACTS[key], value)
                                for key, value in _MACHINE_VALUES.items()},
             "combobox caption/value readback was incomplete"),
            (self.input_values.get("#name") == _FACTS["Name"],
             "the observed name field did not match the synthetic answer"),
            (self.input_values.get("#additional") == _FACTS["Additional question"],
             "the observed additional-answer field did not match the synthetic answer"),
            (self.input_values.get("#location") == _FACTS["Where are you located?"],
             "the observed location field did not match the synthetic answer"),
            (self.input_values.get("#phone-country")
             == _FACTS["What is your phone country?"],
             "the observed phone-country field did not match the synthetic answer"),
            (resume_name == "Synthetic-Resume.pdf" and resume_bytes == _RESUME_BYTES,
             "the staged synthetic résumé did not match"),
        )
        self.post_rejection = ""
        for passed, reason in checks:
            if not passed:
                self.post_rejection = reason
                return False
        self.post_count = 1
        self.post_payload = dict(payload)
        return True

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
            {"action": "fill", "selector": "#additional",
             "fact": "Additional question"},
            {"action": "combobox", "selector": "#location",
             "fact": "Where are you located?"},
            {"action": "combobox", "selector": "#phone-country",
             "fact": "What is your phone country?"},
            {"action": "combobox", "selector": "#work-authorization",
             "fact": "Are you authorized to work?"},
            {"action": "upload", "selector": "#resume"},
            {"action": "submit", "selector": "#submit"},
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
                return observed

            async def page(self):
                if not state.navigations or not authorized(state):
                    raise PermissionError("Fixture cannot inspect another session.")
                observed = await raw.page()
                state.observe_page(observed)
                if not isinstance(observed, dict) or observed.get("url") != URL:
                    raise PermissionError("Fixture page escaped the fixed job URL.")
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
                if args == ("click", "#submit"):
                    state.submit_call_entered += 1
                    expected_commits = {key: (_FACTS[key], value)
                                         for key, value in _MACHINE_VALUES.items()}
                    if (not state.hold or not state.uploaded
                            or state.committed != expected_commits
                            or state.input_values.get("#name") != _FACTS["Name"]
                            or state.input_values.get("#additional")
                               != _FACTS["Additional question"]):
                        state.submit_guard = "failed: required fixture state was not read back"
                        raise PermissionError("Fixture submit lacks exact committed synthetic facts.")
                    state.submit_guard = "passed"
                await raw.call(*args, **kwargs)
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
                elif args == ("click", "#submit"):
                    state.submit_ipc += 1

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
        if (self.path != _PATH or self.headers.get("Host") != "127.0.0.1:18789"):
            if type(state) is _SyntheticRun:
                state.post_rejection = "request target or Host did not match the fixed fixture"
            self._record_post_receipt(state, 404)
            self._reject()
            return
        content_type = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not content_type.lower().startswith("multipart/form-data;") or not 0 < length <= 16384:
                raise ValueError("Invalid synthetic form encoding")
            body = self.rfile.read(length)
            from email import policy
            from email.parser import BytesParser
            envelope = (b"Content-Type: " + content_type.encode("ascii")
                        + bytes([13, 10]) + b"MIME-Version: 1.0" + bytes([13, 10, 13, 10]))
            message = BytesParser(policy=policy.default).parsebytes(envelope + body)
            if not message.is_multipart():
                raise ValueError("Synthetic POST is not multipart")
            payload, resume_name, resume_bytes = {}, "", b""
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    raise ValueError("Invalid multipart field")
                name = part.get_param("name", header="content-disposition")
                value = part.get_payload(decode=True) or b""
                if name == "resume" and not resume_name:
                    resume_name = part.get_filename() or ""
                    resume_bytes = value
                elif isinstance(name, str) and name not in payload:
                    payload[name] = value.decode("utf-8", errors="strict")
                else:
                    raise ValueError("Duplicate synthetic form field")
        except (ValueError, UnicodeDecodeError, LookupError):
            if type(state) is _SyntheticRun:
                state.post_rejection = "invalid multipart request encoding or fields"
            self._record_post_receipt(state, 400)
            self._reject(400)
            return
        if type(state) is not _SyntheticRun or not state.record_post(
                payload, resume_name=resume_name, resume_bytes=resume_bytes):
            self._record_post_receipt(state, 405)
            self._reject(405)
            return
        response = b"<h1>Thank you for applying</h1>"
        self._record_post_receipt(state, 200)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

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
                expected_commits = {key: (_FACTS[key], value)
                                     for key, value in _MACHINE_VALUES.items()}
                ok = (result.get("status") == "applied"
                      and state.fills == 4 and state.decisions == 7
                      and state.committed == expected_commits
                      and state.uploaded and state.submit_ipc == 1
                      and state.post_count == 1
                      and state.post_payload == _POST_FIELDS)
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
                        "outcome": "not_committed",
                        "visible_text": "", "selected_value": "",
                        "hidden_value": "",
                        "reason": "no BrowserSkill page readback was available",
                    })
                    outcomes.append({"field": key, **observation})
                outcomes.append({
                    "field": "Résumé",
                    "outcome": "uploaded" if state.uploaded else "not_reached",
                })
                form_question = result.get("form_question") or {}
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
                print(json.dumps({"fixture": "native-form", "submitted": ok,
                                  "status": result.get("status"),
                                  "comboboxes": len(state.committed),
                                  "post_received": state.post_count == 1,
                                  "gate": gate, "fields": outcomes,
                                  "approval_question_collisions": collisions,
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
