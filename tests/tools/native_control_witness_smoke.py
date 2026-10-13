"""Manual, post-#31-merge real-Chromium witness smoke. Never collected by pytest.

Invoke ONLY in the primary checkout, by the operator/firefighter:
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=src .venv/bin/python tests/tools/native_control_witness_smoke.py

The parent imports stdlib only. A clean-env child enters a fresh Linux user and
network namespace *before* importing production code or starting Chrome. This
checks the imported JavaScript in Chrome, NOT BrowserSkill IPC or owner grants.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

PASS = "PASS native-control-witness: loopback-only; native-state-matrix verified"
FAIL = "FAIL native-control-witness:"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TIMEOUT = 75


class SmokeFailure(Exception):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SmokeFailure(reason)


def _namespace_id(name: str) -> str:
    return os.readlink(f"/proc/self/ns/{name}")


def _clean_environment(root: Path) -> dict[str, str]:
    project = Path(__file__).resolve().parents[2]
    for name in ("home", "config", "cache"):
        (root / name).mkdir(mode=0o700)
    return {
        "PATH": SYSTEM_PATH,
        "PYTHONPATH": str(project / "src"),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }


def _parent() -> None:
    require(sys.platform.startswith("linux"), "Linux user/network namespace required")
    unshare = shutil.which("unshare", path=SYSTEM_PATH)
    require(bool(unshare), "OS unshare executable unavailable")
    with tempfile.TemporaryDirectory(prefix="native-witness-") as temp:
        env = _clean_environment(Path(temp))
        cmd = [unshare, "--user", "--map-root-user", "--net", "--fork",
               "--kill-child", "--", sys.executable, str(Path(__file__).resolve()),
               "--isolated-child", _namespace_id("user"), _namespace_id("net")]
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            try:
                stdout, _stderr = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise SmokeFailure("namespace child timed out") from exc
            lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
            if proc.returncode == 0 and lines == [PASS]:
                print(PASS)
                return
            if len(lines) == 1 and lines[0].startswith(FAIL) and proc.returncode != 0:
                raise SmokeFailure(lines[0][len(FAIL):].strip())
            raise SmokeFailure("namespace/import/browser failed or result was not verifiable")
        finally:
            # All descendants share the unshare process group, even after a
            # timed-out or crashed child. Never target unrelated process names.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _ip_json(ip: str, *args: str) -> list[dict]:
    result = subprocess.run([ip, "-j", *args], capture_output=True,
                            text=True, timeout=5, check=True)
    value = json.loads(result.stdout)
    require(isinstance(value, list), "unverifiable network interface/route inventory")
    return value


def _verify_namespace(parent_user: str, parent_net: str) -> None:
    require(_namespace_id("user") != parent_user, "user namespace not isolated")
    require(_namespace_id("net") != parent_net, "network namespace not isolated")
    ip = shutil.which("ip", path=SYSTEM_PATH)
    require(bool(ip), "OS ip utility unavailable for route verification")
    subprocess.run([ip, "link", "set", "lo", "up"], check=True,
                   timeout=5, capture_output=True)
    links = _ip_json(ip, "link", "show")
    require(len(links) == 1 and links[0].get("ifname") == "lo"
            and "UP" in links[0].get("flags", [])
            and "LOOPBACK" in links[0].get("flags", []),
            "namespace has another interface or loopback is not enabled")
    addr = _ip_json(ip, "-4", "addr", "show", "dev", "lo")
    require(len(addr) == 1 and any(
        x.get("local") == "127.0.0.1" for x in addr[0].get("addr_info", [])),
        "namespace has no enabled IPv4 loopback address")
    for family in ("-4", "-6"):
        routes = _ip_json(ip, family, "route", "show", "table", "all")
        require(all(route.get("dev") == "lo" and
                    route.get("dst") not in ("default", "0.0.0.0/0", "::/0")
                    for route in routes),
                "namespace has a non-loopback or default route")


def _browser_binary() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome"):
        path = shutil.which(name, path=SYSTEM_PATH)
        if path:
            return path
    raise SmokeFailure("isolated OS Chrome/Chromium unavailable")


class CDP:
    """Minimal NUL-framed CDP pipe with synchronous nested event servicing."""

    def __init__(self, receive_fd: int, send_fd: int, fixture_url: str):
        self.receive_fd = receive_fd
        self.send_fd = send_fd
        self.fixture_url = fixture_url
        self.buffer = b""
        self.next_id = 0
        self.pending: dict[int, dict] = {}
        self.prepared: set[str] = set()
        self.discovered: dict[str, str] = {}
        self.intercepted_targets: set[str] = set()
        self.page_session: str | None = None
        self.page_target: str | None = None
        self.fetch: list[tuple[str, str, str]] = []
        self.network: list[tuple[str, str, str]] = []
        self.redirects: list[str] = []
        self.forbidden: list[str] = []
        self.loaded = False
        self.main_frame_url = ""
        self.deadline = time.monotonic() + 25

    def _read(self) -> dict:
        while b"\0" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            require(remaining > 0, "Chrome/CDP timeout")
            readable, _, _ = select.select([self.receive_fd], [], [], remaining)
            require(bool(readable), "Chrome/CDP response timeout")
            part = os.read(self.receive_fd, 65536)
            require(bool(part), "Chrome/CDP pipe closed")
            self.buffer += part
            require(len(self.buffer) < 10_000_000, "oversized CDP response")
        packet, self.buffer = self.buffer.split(b"\0", 1)
        value = json.loads(packet.decode("utf-8"))
        require(isinstance(value, dict), "malformed Chrome/CDP message")
        return value

    def _write(self, message: dict):
        frame = json.dumps(message, separators=(",", ":")).encode() + b"\0"
        while frame:
            written = os.write(self.send_fd, frame)
            require(written > 0, "Chrome/CDP write failed")
            frame = frame[written:]

    def call(self, method: str, params: dict | None = None,
             session: str | None = None) -> dict:
        self.next_id += 1
        index = self.next_id
        message = {"id": index, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self._write(message)
        while index not in self.pending:
            self._dispatch(self._read())
        response = self.pending.pop(index)
        require("error" not in response, f"CDP {method} unsupported or failed")
        return response.get("result", {})

    def _prepare(self, session: str, target_type: str, waiting: bool, target: str):
        require(target_type in {"page", "iframe", "worker", "shared_worker",
                                "service_worker"},
                f"unverifiable child target type {target_type}")
        require(session not in self.prepared, "duplicate CDP child-target session")
        # Both domains must ACK *before* target release or first navigation.
        self.call("Network.enable", session=session)
        self.call("Fetch.enable", {"patterns": [{"urlPattern": "*",
                                                 "requestStage": "Request"}]}, session)
        self.call("Target.setAutoAttach", {"autoAttach": True,
            "waitForDebuggerOnStart": True, "flatten": True}, session)
        if target_type in {"page", "iframe"}:
            self.call("Page.enable", session=session)
        self.call("Runtime.enable", session=session)
        self.prepared.add(session)
        self.intercepted_targets.add(target)
        if target_type == "page":
            require(self.page_session is None or self.page_session == session,
                    "multiple independently navigable page targets")
            self.page_session = session
            self.page_target = target
        if waiting:
            self.call("Runtime.runIfWaitingForDebugger", session=session)

    def _dispatch(self, message: dict):
        if isinstance(message.get("id"), int):
            self.pending[message["id"]] = message
            return
        method = message.get("method", "")
        args = message.get("params") or {}
        session = message.get("sessionId")
        if method == "Target.targetCreated":
            info = args.get("targetInfo") or {}
            if info.get("type") in {"page", "iframe", "worker",
                                    "shared_worker", "service_worker"}:
                self.discovered[info.get("targetId", "")] = info["type"]
        elif method == "Target.attachedToTarget":
            info = args.get("targetInfo") or {}
            self._prepare(args["sessionId"], info.get("type", ""),
                          args.get("waitingForDebugger") is True,
                          info.get("targetId", ""))
        elif method == "Target.detachedFromTarget":
            self.forbidden.append("intercepted target detached before validation")
        elif method == "Fetch.requestPaused":
            req = args.get("request") or {}
            url, verb = req.get("url", ""), req.get("method", "")
            kind = args.get("resourceType", "")
            self.fetch.append((url, verb, kind))
            if url == self.fixture_url and verb == "GET" and kind == "Document":
                self.call("Fetch.continueRequest", {"requestId": args["requestId"]}, session)
            else:
                self.forbidden.append(f"Fetch rejected {verb} {kind} destination {url[:200]!r}")
                self.call("Fetch.failRequest", {"requestId": args["requestId"],
                                                 "errorReason": "BlockedByClient"}, session)
        elif method == "Network.requestWillBeSent":
            request = args.get("request") or {}
            url, verb = request.get("url", ""), request.get("method", "")
            kind = args.get("type", "")
            self.network.append((url, verb, kind))
            if args.get("redirectResponse") is not None:
                self.redirects.append(url[:200])
            if url != self.fixture_url or verb != "GET" or kind != "Document":
                self.forbidden.append(f"Network observed {verb} {kind} destination {url[:200]!r}")
        elif method == "Page.frameNavigated" and session == self.page_session:
            frame = args.get("frame") or {}
            if not frame.get("parentId"):
                self.main_frame_url = frame.get("url", "")
        elif method == "Page.loadEventFired" and session == self.page_session:
            if self.main_frame_url == self.fixture_url:
                self.loaded = True
        elif method in {"Inspector.targetCrashed", "Target.targetCrashed"}:
            self.forbidden.append("browser page target crashed")

    def until_loaded(self):
        while not self.loaded:
            self._dispatch(self._read())

    def evaluate(self, expression: str) -> dict:
        result = self.call("Runtime.evaluate", {"expression": expression,
            "returnByValue": True, "awaitPromise": False}, self.page_session)
        require("exceptionDetails" not in result,
                "imported witness or independent read-only DOM check threw")
        remote = result.get("result") or {}
        require("value" in remote and isinstance(remote["value"], dict),
                "real Chrome did not return a DOM observation object")
        return remote["value"]


_DOM_TRUTH = r"""(() => {
  const ids = __IDS__;
  const form = document.getElementById('witness-form');
  const tidy = s => (s || '').replace(/\s+/g, ' ').trim();
  return {
    url: location.href,
    forms: document.querySelectorAll('#witness-form').length,
    frames: document.querySelectorAll('iframe,frame').length,
    scripts: document.querySelectorAll('script').length,
    action: form && form.action,
    order: form && Array.from(form.querySelectorAll('input,textarea,select'), e => e.id),
    scroll: { width: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
      height: document.documentElement.scrollHeight,
      viewport: innerHeight },
    controls: ids.map(id => {
      const elements = document.querySelectorAll('#'+CSS.escape(id));
      const e = elements[0], r = e.getBoundingClientRect();
      return { id, count: elements.length, tag: e.tagName.toLowerCase(),
        type: e.tagName.toLowerCase() === 'input' ? e.type : e.tagName.toLowerCase(),
        form: e.form === form, action: e.form && e.form.action,
        disabled: !!e.disabled || e.matches(':disabled'),
        readonly: !!e.readOnly || e.hasAttribute('readonly'),
        inert: !!e.closest('[inert]'),
        label: tidy(Array.from(e.labels || [], n => n.innerText).join(' ')),
        options: e.options ? Array.from(e.options,
          o => ({value:o.value, label:tidy(o.textContent)})) : [],
        width: r.width, height: r.height, right: r.right, top: r.top,
        position: getComputedStyle(e).position };
    })
  };
})()"""


def _assert_matrix(observed: dict, truth: dict, fixture, url: str):
    cases = fixture.CASES
    ids = [item[0] for item in cases]
    require(observed.get("url") == url and truth.get("url") == url,
            "current URL does not match the exact fixture GET")
    require(truth.get("frames") == 0 and truth.get("scripts") == 0,
            "fixture unexpectedly permits a script or child frame")
    require(truth.get("forms") == 1 and truth.get("action") == url,
            "native witness form is not unique or has a different destination")
    require(truth.get("order") == ids, "independent DOM control order mismatch")
    controls = observed.get("controls")
    actual = truth.get("controls")
    require(isinstance(controls, list) and len(controls) == len(cases)
            and isinstance(actual, list) and len(actual) == len(cases),
            "witness omitted, duplicated or added native controls")
    zero = actual[ids.index("zero-area")]
    off = actual[ids.index("offcanvas")]
    below = actual[ids.index("below-fold-scrollable")]
    layout = truth["scroll"]
    require(zero["width"] * zero["height"] == 0,
            "zero-area: decoy unexpectedly has layout area")
    require(off["width"] > 0 and off["height"] > 0 and off["right"] < 0
            and off["position"] == "fixed" and layout["width"] <= layout["client"],
            "offcanvas: decoy is not completely non-scrollable off-canvas")
    require(below["top"] > layout["viewport"] and
            layout["height"] > layout["viewport"],
            "below-fold-scrollable: ordinary scrolling cannot reach the control")
    flags = {
        "readonly-input": "readonly", "disabled-input": "disabled",
        "inert-input": "inert", "inert-parent": "inert",
    }
    for (id_, tag, type_, candidate), got, dom in zip(cases, controls, actual):
        require(dom.get("id") == id_ and dom.get("count") == 1
                and dom.get("tag") == tag and dom.get("type") == type_
                and dom.get("form") is True and dom.get("action") == url,
                f"{id_}: independent native identity or form mismatch")
        for key, expected in (("selector", "#"+id_), ("unique", True),
                              ("tag", tag), ("type", type_), ("in_form", True),
                              ("form_selector", "#witness-form"),
                              ("unique_form", True), ("form_action", url),
                              ("formaction", "")):
            require(type(got.get(key)) is type(expected) and got[key] == expected,
                    f"{id_}: witness {key} mismatch")
        for name in ("disabled", "readonly", "inert"):
            expected = flags.get(id_) == name
            require(dom.get(name) is expected and got.get(name) is expected,
                    f"{id_}: native {name} mismatch")
        expected_visible = id_ not in {
            "display-none-self", "visibility-hidden-self",
            "display-none-parent", "visibility-hidden-parent",
            "zero-area", "offcanvas"}
        require(got.get("visible") is expected_visible,
                f"{id_}: production visible flag disagrees with fixture geometry/CSS")
        witness_candidate = (got.get("visible") is True
                             and got.get("disabled") is False
                             and got.get("readonly") is False
                             and got.get("inert") is False
                             and got.get("unique") is True
                             and got.get("unique_form") is True)
        require(witness_candidate is candidate, f"{id_}: witness candidate mismatch")
        expected_text = (fixture.CHECKBOX_CAPTION if id_ == "check-detail" else
                         fixture.RADIO_CAPTIONS[0] if id_ == "radio-alpha" else
                         fixture.RADIO_CAPTIONS[1] if id_ == "radio-beta" else "")
        require(dom.get("label") == expected_text if expected_text else True,
                f"{id_}: fixture native label mismatch")
        require(got.get("visible_text") == expected_text,
                f"{id_}: verbatim native checkbox/radio caption mismatch")
        expected_options = ([{"value": value, "label": label}
                             for value, label in fixture.SELECT_OPTIONS]
                            if id_ == "selection" else [])
        require(dom.get("options") == expected_options
                and got.get("options") == expected_options,
                f"{id_}: full ordered select options mismatch")
    require(len([c for c in controls if c.get("type") == "radio"]) == 2,
            "native radio group membership incomplete")
    require("grantable" not in observed and all("grantable" not in c for c in controls),
            "witness invented an authorization/grant field")


def _run_chrome(fixture, production_script: str, browser: str):
    from threading import Thread
    server, recorder, url = fixture.server()
    thread = Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        with tempfile.TemporaryDirectory(prefix="profile-", dir=os.environ["HOME"]) as profile:
            # Chrome's POSIX debugging pipe requires child descriptors 3 and 4.
            # Duplicate endpoints above 10 so fd remapping never clobbers them.
            with contextlib.ExitStack() as stack:
                to_chrome_read, to_chrome_write = os.pipe()
                from_chrome_read, from_chrome_write = os.pipe()
                for fd in (to_chrome_read, to_chrome_write,
                           from_chrome_read, from_chrome_write):
                    stack.callback(_close_fd, fd)
                chrome_in = fcntl.fcntl(to_chrome_read, fcntl.F_DUPFD, 10)
                chrome_out = fcntl.fcntl(from_chrome_write, fcntl.F_DUPFD, 10)
                stack.callback(_close_fd, chrome_in)
                stack.callback(_close_fd, chrome_out)

                command = [browser, "--headless=new", "--no-sandbox",
                    "--disable-background-networking", "--disable-extensions",
                    "--disable-component-update", "--disable-sync",
                    "--no-first-run", "--no-default-browser-check",
                    "--no-proxy-server", "--disable-gpu", "--disable-breakpad",
                    "--remote-debugging-pipe", "--no-startup-window",
                    f"--user-data-dir={profile}"]
                # posix_spawn dup2 actions install FD 3/4 after all close-on-exec
                # handling; Popen preexec_fn + pass_fds can close those FDs.
                devnull = "/dev/null"
                actions = [
                    (os.POSIX_SPAWN_OPEN, 0, devnull, os.O_RDONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 1, devnull, os.O_WRONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 2, devnull, os.O_WRONLY, 0),
                    (os.POSIX_SPAWN_DUP2, chrome_in, 3),
                    (os.POSIX_SPAWN_DUP2, chrome_out, 4),
                    (os.POSIX_SPAWN_CLOSE, chrome_in),
                    (os.POSIX_SPAWN_CLOSE, chrome_out),
                ]
                chrome_pid = os.posix_spawn(browser, command, dict(os.environ),
                                           file_actions=actions)
                try:
                    thread.start()
                    started = True
                    os.close(to_chrome_read)
                    os.close(from_chrome_write)
                    os.close(chrome_in)
                    os.close(chrome_out)
                    cdp = CDP(from_chrome_read, to_chrome_write, url)
                    cdp.call("Browser.getVersion")
                    cdp.call("Target.setDiscoverTargets", {"discover": True})
                    cdp.call("Target.setAutoAttach", {"autoAttach": True,
                        "waitForDebuggerOnStart": True, "flatten": True})
                    target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                    if cdp.page_target != target:
                        cdp.call("Target.attachToTarget", {"targetId": target,
                            "flatten": True})
                    require(bool(cdp.page_session) and cdp.page_target == target
                            and cdp.page_session in cdp.prepared,
                            "page was not intercepted before navigation")
                    cdp.loaded = False
                    cdp.main_frame_url = ""
                    navigation = cdp.call("Page.navigate", {"url": url}, cdp.page_session)
                    require(not navigation.get("errorText"),
                            "Chrome refused the synthetic fixture navigation")
                    cdp.until_loaded()
                    require(not cdp.forbidden and not cdp.redirects,
                            (cdp.forbidden or cdp.redirects or ["unverified network attempt"])[0])
                    ids = [case[0] for case in fixture.CASES]
                    dom_truth = cdp.evaluate(_DOM_TRUTH.replace("__IDS__", json.dumps(ids)))
                    # Imported production JS is evaluated verbatim, not wrapped or copied.
                    witness = cdp.evaluate(production_script)
                    # An extra protocol round-trip pumps pending request/target events.
                    active = cdp.call("Target.getTargets").get("targetInfos", [])
                    types = {"page", "iframe", "worker", "shared_worker", "service_worker"}
                    require(isinstance(active, list) and all(
                        info.get("targetId") in cdp.intercepted_targets
                        for info in active if info.get("type") in types),
                        "active page/frame/worker target was not intercepted")
                    require(not cdp.forbidden and not cdp.redirects,
                            (cdp.forbidden or cdp.redirects or ["unverified network attempt"])[0])
                    require(set(cdp.discovered) <= cdp.intercepted_targets,
                            "a page/frame/worker target lacked pre-navigation interception")
                    require(len(cdp.fetch) == len(cdp.network) == 1
                            and cdp.fetch[0] == cdp.network[0] ==
                                (url, "GET", "Document"),
                            "CDP Fetch/Network destination or interception log mismatch")
                    require(recorder.snapshot() ==
                            [("GET", fixture.PATH, url.split('/')[2], True)],
                            "fixture GET log differs from intercepted page requests")
                    _assert_matrix(witness, dom_truth, fixture, url)
                finally:
                    try:
                        os.kill(chrome_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    until = time.monotonic() + 6
                    while time.monotonic() < until:
                        ended, _ = os.waitpid(chrome_pid, os.WNOHANG)
                        if ended == chrome_pid:
                            break
                        time.sleep(0.05)
                    else:
                        try:
                            os.kill(chrome_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.waitpid(chrome_pid, 0)
    finally:
        if started:
            server.shutdown()
            thread.join(timeout=3)
        server.server_close()
        require(not thread.is_alive(), "fixture server thread did not stop")


def _child(parent_user: str, parent_net: str) -> None:
    require(os.environ.get("PYTHON_DOTENV_DISABLED") == "1", "dotenv not disabled")
    _verify_namespace(parent_user, parent_net)
    browser = _browser_binary()
    # This is the very first tools/core import; all process/browser child
    # environment settings and namespace checks precede it.
    from tools.browser_skill_apply import _FIXED_CONTROL_WITNESS
    import native_control_witness_fixture as fixture
    require(isinstance(_FIXED_CONTROL_WITNESS, str) and _FIXED_CONTROL_WITNESS,
            "merged production witness symbol missing")
    _run_chrome(fixture, _FIXED_CONTROL_WITNESS, browser)
    _verify_namespace(parent_user, parent_net)


def main() -> int:
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--isolated-child":
            _child(sys.argv[2], sys.argv[3])
            print(PASS)
        elif len(sys.argv) == 1:
            _parent()
        else:
            raise SmokeFailure("unexpected arguments (manual command only)")
        return 0
    except SmokeFailure as exc:
        print(f"{FAIL} {exc}")
        return 1
    except (Exception, KeyboardInterrupt):
        print(f"{FAIL} unavailable namespace/browser/import or unsupported assertion")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
