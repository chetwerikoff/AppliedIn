"""Standalone, web-only OpenCode discovery using the existing ChatGPT login."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from jsonschema import ValidationError, validate

from discovery.claude_search import TIMEOUT, watch_cancellation


def search_env(cwd):
    # An allowlist excludes provider keys AND inherited OpenCode config/agent
    # overrides. The default data location retains OAuth; a temporary DB loses it.
    env = {k: v for k, v in os.environ.items() if k in (
        "PATH", "HOME", "XDG_DATA_HOME", "LANG", "LC_ALL", "SYSTEMROOT",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    )}
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "OPENCODE_CONFIG_DIR"):
        directory = Path(cwd) / key.lower()
        directory.mkdir()
        env[key] = str(directory)
    env.update(
        TMPDIR=str(cwd),
        OPENCODE_DISABLE_PROJECT_CONFIG="1",
        OPENCODE_CONFIG_CONTENT=json.dumps({
            "permission": {"*": "deny", "websearch": "allow", "webfetch": "allow"},
            "plugin": [],
            "mcp": {},
            "provider": {"openai": {
                "options": {"baseURL": "https://chatgpt.com/backend-api/codex"},
                "models": {"gpt-6.1-sol": {"limit": {
                    "context": 272000, "input": 272000, "output": 128000,
                }}},
            }},
        }),
    )
    return env


class SearchEvents:
    def __init__(self, progress, schema, model):
        self.progress, self.schema, self.model = progress, schema, model
        self.queries, self.sources, self.sessions = [], set(), set()
        self.text = ""
        self.complete = self.failed = False

    def accept(self, event):
        session = event.get("sessionID")
        if isinstance(session, str) and session.startswith("ses_"):
            self.sessions.add(session)
        kind, part = event.get("type"), event.get("part") or {}
        if kind == "error":
            self.failed = True
        elif kind == "step_start":
            self.complete = False
            self.text = ""
        elif kind == "step_finish":
            self.complete = part.get("reason") == "stop"
        elif kind == "text":
            self.text += part.get("text", "")
        elif kind == "tool_use":
            state = part.get("state") or {}
            if state.get("status") == "error" and part.get("tool") in ("websearch", "webfetch"):
                self.failed = True
            if state.get("status") != "completed":
                return
            inputs = state.get("input") or {}
            if part.get("tool") == "websearch":
                query = inputs.get("query")
                if isinstance(query, str) and query.strip():
                    self.queries.append(query)
                    self.progress(f"Searched: {query}")
            elif part.get("tool") == "webfetch":
                url = inputs.get("url")
                if isinstance(url, str) and url.startswith(("https://", "http://")):
                    self.sources.add(url)
                    self.progress("Read a posting from the search results")

    def finish(self):
        parsed = None
        try:
            parsed = json.loads(self.text)
            validate(parsed, self.schema)
            valid = isinstance(parsed, dict) and isinstance(parsed.get("jobs"), list)
        except (ValueError, ValidationError):
            valid = False
        if not self.complete or self.failed or not self.queries or not valid:
            raise ValueError(
                "OpenCode did not complete a web search with a valid job list. "
                "Check your OpenCode ChatGPT login and retry."
            )
        return {"parsed": parsed, "source_urls": self.sources,
                "queries": self.queries, "model": self.model}


def run_search(prompt, schema, progress, *, cancelled=None):
    from core.config import get_settings
    from core.steering import prompt as steer

    binary = shutil.which("opencode2")
    if not binary:
        raise ValueError("Install OpenCode CLI (opencode2) and sign in with your ChatGPT account.")
    model = get_settings().search_model
    prompt = steer(prompt) + (
        "\nUse websearch, and open EVERY returned posting URL with webfetch. "
        "Only completed webfetch URLs can be imported. Return ONLY JSON matching this schema:\n"
        + json.dumps(schema)
    )
    with tempfile.TemporaryDirectory(prefix="appliedin-opencode-search-") as cwd:
        env = search_env(cwd)
        events = SearchEvents(progress, schema, model)
        progress(f"OpenCode is searching with {model} using your ChatGPT login…")
        expired = threading.Event()
        try:
            with tempfile.TemporaryFile() as stderr, subprocess.Popen(
                [binary, "run", "--standalone", "--model", model, "--format", "json", prompt],
                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=stderr, text=True,
            ) as proc:

                def expire():
                    if proc.poll() is None:
                        expired.set()
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass

                timer = threading.Timer(TIMEOUT, expire)
                timer.daemon = True
                timer.start()
                cancellation = watch_cancellation(proc, cancelled)
                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict):
                            events.accept(event)
                    code = proc.wait()
                finally:
                    timer.cancel()
                    cancellation.set()
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                if expired.is_set():
                    raise ValueError("OpenCode search timed out; incomplete results were not imported.")
                if cancelled and cancelled():
                    raise ValueError("OpenCode search cancelled; incomplete results were not imported.")
                if code:
                    raise ValueError("OpenCode search failed. Check your ChatGPT login and usage, then retry.")
            return events.finish()
        except OSError as exc:
            raise ValueError("Could not start OpenCode search. Check opencode2 installation and retry.") from exc
        finally:
            # OAuth lives in the default DB in v2.2.5. Delete only sessions this
            # private run actually emitted, including failed/cancelled searches.
            for session in events.sessions:
                try:
                    cleanup = subprocess.run(
                        [binary, "session", "delete", "--standalone", session],
                        cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                        capture_output=True, timeout=20,
                    )
                    if cleanup.returncode:
                        raise ValueError("OpenCode could not delete its search session; retry cleanup.")
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ValueError("OpenCode could not delete its search session; retry cleanup.") from exc
