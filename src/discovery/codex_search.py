"""Read-only Codex CLI discovery using the user's ChatGPT login."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from discovery.claude_search import TIMEOUT, watch_cancellation


class SearchEvents:
    def __init__(self, progress):
        self.progress = progress
        self.queries = []
        self.sources = set()
        self.parsed = None
        self.complete = False

    def accept(self, event):
        if event.get("type") == "turn.completed":
            self.complete = True
        elif event.get("type") in ("turn.failed", "error"):
            self.complete = False
        if event.get("type") != "item.completed":
            return
        item = event.get("item", {})
        if item.get("type") == "web_search":
            action = item.get("action", {})
            query = action.get("query") or item.get("query")
            if query and action.get("type") == "search":
                self.queries.append(query)
                self.progress(f"Searched: {query}")
            url = action.get("url") if action.get("type") in ("open_page", "find") else None
            # This CLI version exposes a completed web open as action=other,
            # query=<URL>. Do not accept a URL from an agent message or a search
            # query: neither proves the worker actually read that page.
            if action.get("type") == "other" and str(query).startswith("https://"):
                url = query
            if url:
                self.sources.add(url)
                self.progress("Read a posting from the search results")
        elif item.get("type") == "agent_message":
            try:
                self.parsed = json.loads(item.get("text", ""))
            except ValueError:
                pass

    def finish(self):
        if (
            not self.complete
            or not self.queries
            or not isinstance(self.parsed, dict)
            or not isinstance(self.parsed.get("jobs"), list)
        ):
            raise ValueError(
                "Codex did not complete a web search. Check your Codex login and retry."
            )
        return {"parsed": self.parsed, "source_urls": self.sources, "queries": self.queries}


def run_search(prompt, schema, progress, *, cancelled=None):
    if not shutil.which("codex"):
        raise ValueError("Install Codex CLI and run codex login with your ChatGPT account.")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENAI_", "ANTHROPIC_")) and k != "CODEX_API_KEY"
    }
    with tempfile.TemporaryDirectory(prefix="appliedin-codex-search-") as cwd:
        login = subprocess.run(
            ["codex", "login", "status"],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        if (
            login.returncode
            or "logged in using chatgpt" not in (login.stdout + login.stderr).lower()
        ):
            raise ValueError(
                "Career Ops needs a ChatGPT login for Codex. "
                "Run codex login and sign in with ChatGPT."
            )
        schema_path = Path(cwd) / "result-schema.json"
        schema_path.write_text(json.dumps(schema))
        command = [
            "codex",
            "--search",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--disable",
            "shell_tool",
            "--disable",
            "apps",
            "--disable",
            "multi_agent",
            "--disable",
            "memories",
            "--json",
            "--output-schema",
            str(schema_path),
            prompt + "\nOpen EVERY posting URL with the web tool before returning it. "
            "Only opened posting URLs can be imported. Do not use local files or other tools.",
        ]
        events = SearchEvents(progress)
        progress("Codex is searching with your ChatGPT login…")
        expired = threading.Event()
        with (
            tempfile.TemporaryFile() as stderr,
            subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            ) as proc,
        ):

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
                raise ValueError(
                    "Codex search timed out. Retry; incomplete results were not imported."
                )
            if code:
                raise ValueError(
                    "Codex search failed. Check your ChatGPT usage and update Codex CLI, "
                    "then retry."
                )
        return events.finish()
