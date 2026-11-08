#!/usr/bin/env python3
"""Solve a visible CAPTCHA on a board agent's application tab through CapSolver.

Owner decision 2026-11-08: board agents may pass a visible CAPTCHA on any job
site with CapSolver, except LinkedIn and login / sign-up / password pages.
Those exclusions are enforced here, in code, not in the agent brief.

Usage (board agent, own bsk session only):

    .venv/bin/python scripts/captcha_solve.py --session <SID> [--tab-id <TAB>] [--dry-run]

Flow: `bsk cooperate sync` + `bsk evaluate` to find the widget, guard checks,
CapSolver createTask/getTaskResult (token API only, nothing but the page URL
and the site key leave this machine), then a second sync + evaluate that puts
the token into the response field and calls the widget callback only when
exactly one callback exists. It never clicks Submit.

Exit codes:
    0  token injected
    2  refused by guard (LinkedIn, login/sign-up/password page)
    3  no visible challenge on the page (nothing to do)
    4  unsupported or ambiguous widget (hand the tab to the owner)
    5  solver or browser error (hand the tab to the owner)

Every run appends one line (no token) to <local_dir>/captcha-log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

API = "https://api.capsolver.com"
POLL_SECONDS = 3.0
POLL_TIMEOUT = 150.0

EXIT_OK, EXIT_REFUSED, EXIT_NONE, EXIT_UNSUPPORTED, EXIT_ERROR = 0, 2, 3, 4, 5

BLOCKED_HOSTS = ("linkedin.com", "lnkd.in", "licdn.com")
BLOCKED_URL = re.compile(
    r"(log-?in|sign-?in|sign-?up|register|create-?account|/auth\b|/sso\b|password|forgot)",
    re.IGNORECASE,
)

DETECT_JS = r"""(()=>{
const out={__cs:1,url:location.href,password:!!document.querySelector('input[type=password]'),found:[]};
const onscreen=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);
 let p=e,hidden=false;while(p&&p.nodeType===1){const ps=getComputedStyle(p);if(ps.visibility==='hidden'||ps.display==='none'||ps.opacity==='0'){hidden=true;break}p=p.parentElement}
 return !hidden&&r.width>20&&r.height>20&&r.bottom>0&&r.top<innerHeight+2000&&s.visibility!=='hidden'};
const add=(o)=>{if(o.sitekey)out.found.push(o)};
document.querySelectorAll('[data-sitekey]').forEach(e=>{const c=String(e.className||'');const k=e.getAttribute('data-sitekey');
 if(/h-captcha/.test(c))add({kind:'hcaptcha',sitekey:k,visible:onscreen(e)});
 else if(/cf-turnstile/.test(c))add({kind:'turnstile',sitekey:k,visible:onscreen(e),action:e.getAttribute('data-action')||'',cdata:e.getAttribute('data-cdata')||''});
 else if(/g-recaptcha/.test(c))add({kind:'recaptcha',sitekey:k,size:e.getAttribute('data-size')||'',visible:onscreen(e)});});
document.querySelectorAll('iframe').forEach(f=>{let u;try{u=new URL(f.src,location.href)}catch(e){return}
 const h=u.hostname,p=u.pathname;
 if(/(^|\.)(google\.com|recaptcha\.net)$/.test(h)&&p.indexOf('/recaptcha/')>=0){
  add({kind:'recaptcha',sitekey:u.searchParams.get('k'),enterprise:p.indexOf('/enterprise/')>=0,
   frame:p.indexOf('/bframe')>=0?'bframe':(p.indexOf('/anchor')>=0?'anchor':'other'),
   size:u.searchParams.get('size')||'',visible:onscreen(f)});}
 else if(/(^|\.)hcaptcha\.com$/.test(h)){const m=(u.hash||'').match(/sitekey=([^&]+)/);add({kind:'hcaptcha',sitekey:u.searchParams.get('sitekey')||(m&&m[1]),visible:onscreen(f)});}
 else if(h==='challenges.cloudflare.com'){const m=p.match(/\/(0x[\w-]+)\//);add({kind:'turnstile',sitekey:m&&m[1],visible:onscreen(f),action:'',cdata:''});}});
out.enterpriseLib=!!(window.grecaptcha&&window.grecaptcha.enterprise);
return JSON.stringify(out)})()"""

# Token is JSON-encoded into the expression. A callback is called only when
# exactly one distinct function is found: two would risk a double submission.
INJECT_JS = r"""((t,kind)=>{
const fieldSel=kind==='turnstile'?'input[name="cf-turnstile-response"],textarea[name="cf-turnstile-response"]'
 :'textarea[name="g-recaptcha-response"],textarea[id^="g-recaptcha-response"]';
let n=0;document.querySelectorAll(fieldSel).forEach(e=>{e.value=t;if(e.tagName==='TEXTAREA')e.innerHTML=t;
 e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));n++});
const fns=new Set();
const byName=v=>{if(typeof v==='function')fns.add(v);else if(typeof v==='string'&&v&&typeof window[v]==='function')fns.add(window[v])};
if(kind==='recaptcha'){const seen=new Set();
 const walk=(o,d)=>{if(!o||typeof o!=='object'||d>5||seen.has(o))return;seen.add(o);
  for(const k of Object.keys(o)){let v;try{v=o[k]}catch(e){continue}
   if(k==='callback')byName(v);else if(v&&typeof v==='object')walk(v,d+1)}};
 try{const c=window.___grecaptcha_cfg;if(c&&c.clients)walk(c.clients,0)}catch(e){}
 document.querySelectorAll('.g-recaptcha[data-callback]').forEach(e=>byName(e.getAttribute('data-callback')));}
else{document.querySelectorAll('.cf-turnstile[data-callback]').forEach(e=>byName(e.getAttribute('data-callback')));}
let called=0,cbError='';
if(fns.size===1){try{[...fns][0](t);called=1}catch(e){cbError=String(e).slice(0,200)}}
return JSON.stringify({__cs:1,fields:n,callbacks:fns.size,called,cbError})})"""


class CaptchaError(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------- local state

def local_dir() -> Path:
    env = os.environ.get("APPLIEDIN_LOCAL_DIR")
    if env:
        return Path(env)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from core.config import get_settings  # type: ignore

        return Path(get_settings().local_dir)
    except Exception:
        return Path(__file__).resolve().parents[1] / ".local"


def load_api_key(base: Path) -> str:
    key = os.environ.get("CAPSOLVER_API_KEY", "").strip()
    if key:
        return key
    path = base / "secrets.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    entry = data.get("capsolver")
    if isinstance(entry, dict):
        entry = entry.get("api_key")
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    raise CaptchaError(EXIT_ERROR, f"no CapSolver key: set CAPSOLVER_API_KEY or capsolver.api_key in {path}")


def write_log(base: Path, record: dict) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    try:
        with (base / "captcha-log.jsonl").open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def short_url(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


# ---------------------------------------------------------------- guard

def guard(url: str, has_password: bool) -> None:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host or parts.scheme not in ("http", "https"):
        raise CaptchaError(EXIT_REFUSED, f"not a web page: {url!r}")
    if any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
        raise CaptchaError(EXIT_REFUSED, f"blocked host {host}: LinkedIn CAPTCHA is never solved")
    if BLOCKED_URL.search(parts.path + "?" + parts.query):
        raise CaptchaError(EXIT_REFUSED, "login / sign-up / password page: human gate")
    if has_password:
        raise CaptchaError(EXIT_REFUSED, "page has a password field: human gate")


def pick_challenge(found: list[dict]) -> dict:
    """Return the one visible challenge, or raise. Invisible badges are not challenges."""
    visible = []
    for item in found:
        if not item.get("sitekey") or not item.get("visible"):
            continue
        if item["kind"] == "recaptcha":
            if item.get("frame") == "bframe":
                visible.append(item)  # image challenge popped up
            elif item.get("size") != "invisible" and item.get("frame") in ("anchor", None):
                visible.append(item)  # "I'm not a robot" box
        else:
            visible.append(item)
    if not visible:
        raise CaptchaError(EXIT_NONE, "no visible challenge (an invisible badge needs nothing)")
    kinds = {(v["kind"], v["sitekey"]) for v in visible}
    if len(kinds) > 1:
        raise CaptchaError(EXIT_UNSUPPORTED, f"several different widgets on the page: {sorted(kinds)}")
    chosen = dict(visible[0])
    # An enterprise anchor/bframe anywhere for this key marks the key as enterprise.
    chosen["enterprise"] = any(
        f.get("enterprise") for f in found if f.get("sitekey") == chosen["sitekey"]
    )
    if chosen["kind"] == "hcaptcha":
        raise CaptchaError(EXIT_UNSUPPORTED, "hCaptcha: not solved through CapSolver")
    return chosen


def build_task(challenge: dict, url: str) -> dict:
    kind = challenge["kind"]
    if kind == "recaptcha":
        task = {
            "type": "ReCaptchaV2EnterpriseTaskProxyLess" if challenge.get("enterprise") else "ReCaptchaV2TaskProxyLess",
            "websiteURL": url,
            "websiteKey": challenge["sitekey"],
        }
        if challenge.get("size") == "invisible" or challenge.get("frame") == "bframe":
            task["isInvisible"] = True
        return task
    if kind == "turnstile":
        task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": url, "websiteKey": challenge["sitekey"]}
        meta = {k: challenge[k] for k in ("action", "cdata") if challenge.get(k)}
        if meta:
            task["metadata"] = meta
        return task
    raise CaptchaError(EXIT_UNSUPPORTED, f"unsupported widget {kind}")


# ---------------------------------------------------------------- CapSolver

def _post(path: str, payload: dict, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def solve(task: dict, key: str, *, opener=urllib.request.urlopen, sleep=time.sleep,
          clock=time.monotonic) -> tuple[str, str]:
    created = _post("/createTask", {"clientKey": key, "task": task}, opener)
    if created.get("errorId"):
        raise CaptchaError(EXIT_ERROR, f"createTask: {created.get('errorCode')} {created.get('errorDescription')}")
    task_id = created.get("taskId", "")
    if created.get("status") == "ready" and created.get("solution"):
        return task_id, _token(created["solution"])
    deadline = clock() + POLL_TIMEOUT
    while clock() < deadline:
        sleep(POLL_SECONDS)
        res = _post("/getTaskResult", {"clientKey": key, "taskId": task_id}, opener)
        if res.get("errorId"):
            raise CaptchaError(EXIT_ERROR, f"getTaskResult: {res.get('errorCode')} {res.get('errorDescription')}")
        if res.get("status") == "ready":
            return task_id, _token(res.get("solution") or {})
    raise CaptchaError(EXIT_ERROR, f"CapSolver timeout after {POLL_TIMEOUT:.0f}s (task {task_id})")


def _token(solution: dict) -> str:
    token = solution.get("gRecaptchaResponse") or solution.get("token") or ""
    if not token:
        raise CaptchaError(EXIT_ERROR, "CapSolver returned no token")
    return token


# ---------------------------------------------------------------- bsk

def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], str):
            return obj[key]
        for v in obj.values():
            hit = _find(v, key)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, key)
            if hit:
                return hit
    return None


def _marker_payload(obj):
    """Find the JSON string our scripts return (it carries "__cs":1)."""
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("{") and '"__cs"' in s:
            try:
                return json.loads(s)
            except ValueError:
                return None
        return None
    if isinstance(obj, dict):
        if obj.get("__cs") == 1:
            return obj
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            hit = _marker_payload(v)
            if hit is not None:
                return hit
    return None


def _bsk(args: list[str], runner=subprocess.run) -> dict:
    proc = runner(["bsk", *args, "--json"], capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise CaptchaError(EXIT_ERROR, f"bsk {args[0]} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise CaptchaError(EXIT_ERROR, f"bsk {args[0]}: non-JSON output") from None


def evaluate(session: str, tab: str | None, expression: str, runner=subprocess.run) -> dict:
    sync_args = ["cooperate", "sync", "--session", session]
    if tab:
        sync_args += ["--tab-id", tab]
    sync = _bsk(sync_args, runner)
    context = _find(sync, "context_id")
    observation = sync.get("observation_id") if isinstance(sync, dict) else None
    observation = observation or _find(sync, "observation_id")
    if not context:
        raise CaptchaError(EXIT_ERROR, "bsk cooperate sync returned no context_id")
    args = ["evaluate", "--session", session, "--context", context]
    if observation:
        args += ["--observation", observation]
    if tab:
        args += ["--tab-id", tab]
    args.append(expression)
    result = _marker_payload(_bsk(args, runner))
    if result is None:
        raise CaptchaError(EXIT_ERROR, "bsk evaluate returned no script result")
    return result


# ---------------------------------------------------------------- main

def run(session: str, tab: str | None, dry_run: bool, base: Path) -> tuple[int, dict]:
    log: dict = {"session": session}
    try:
        page = evaluate(session, tab, DETECT_JS)
        url = page.get("url", "")
        log["url"] = short_url(url)
        guard(url, bool(page.get("password")))
        challenge = pick_challenge(page.get("found") or [])
        task = build_task(challenge, url)
        log.update(kind=challenge["kind"], task_type=task["type"])
        if dry_run:
            log["result"] = "dry_run"
            return EXIT_OK, log
        key = load_api_key(base)
        started = time.monotonic()
        task_id, token = solve(task, key)
        log.update(task_id=task_id, solve_seconds=round(time.monotonic() - started, 1))
        injected = evaluate(session, tab, f"{INJECT_JS}({json.dumps(token)},{json.dumps(challenge['kind'])})")
        log.update(fields=injected.get("fields"), callbacks=injected.get("callbacks"),
                   called=injected.get("called"))
        if not injected.get("fields"):
            raise CaptchaError(EXIT_ERROR, "no response field found to put the token into")
        if injected.get("cbError"):
            raise CaptchaError(EXIT_ERROR, f"widget callback threw: {injected['cbError']}")
        if injected.get("callbacks", 0) > 1:
            raise CaptchaError(EXIT_UNSUPPORTED, "token set, but several callbacks exist: none called")
        log["result"] = "injected"
        return EXIT_OK, log
    except CaptchaError as exc:
        log.update(result="refused" if exc.code == EXIT_REFUSED else "failed", reason=exc.reason)
        return exc.code, log


def save_api_key(base: Path, key: str) -> Path:
    """Store the key in <local_dir>/secrets.json, keeping other entries, mode 600."""
    key = key.strip()
    if not key:
        raise CaptchaError(EXIT_ERROR, "empty key")
    path = base / "secrets.json"
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError
    except FileNotFoundError:
        data = {}
    except ValueError:
        raise CaptchaError(EXIT_ERROR, f"{path} is not a JSON object; fix it by hand first") from None
    data["capsolver"] = {"api_key": key}
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def balance(key: str, opener=urllib.request.urlopen) -> float:
    res = _post("/getBalance", {"clientKey": key}, opener)
    if res.get("errorId"):
        raise CaptchaError(EXIT_ERROR, f"getBalance: {res.get('errorCode')} {res.get('errorDescription')}")
    return float(res.get("balance", 0))


def set_key_interactive(base: Path) -> int:
    import getpass

    try:
        key = getpass.getpass("CapSolver API key (input hidden): ")
        path = save_api_key(base, key)
        print(f"saved to {path} (mode 600)")
        print(f"key works, balance: {balance(load_api_key(base)):.2f} USD")
        return EXIT_OK
    except CaptchaError as exc:
        print(f"error: {exc.reason}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # network error on the balance check
        print(f"key saved, balance check failed: {exc}", file=sys.stderr)
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set-key", action="store_true", help="prompt for the CapSolver key (hidden) and save it")
    ap.add_argument("--balance", action="store_true", help="check the stored key and print the balance")
    ap.add_argument("--session", help="your own bsk session id")
    ap.add_argument("--tab-id", help="tab id (default: the session's active tab)")
    ap.add_argument("--dry-run", action="store_true", help="detect and check only, no CapSolver call")
    ns = ap.parse_args(argv)
    base = local_dir()
    if ns.set_key:
        return set_key_interactive(base)
    if ns.balance:
        try:
            print(f"balance: {balance(load_api_key(base)):.2f} USD")
            return EXIT_OK
        except CaptchaError as exc:
            print(f"error: {exc.reason}", file=sys.stderr)
            return exc.code
    if not ns.session:
        ap.error("--session is required")
    code, log = run(ns.session, ns.tab_id, ns.dry_run, base)
    write_log(base, {**log, "exit": code})
    print(json.dumps({"exit": code, **{k: v for k, v in log.items() if k != "session"}}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
