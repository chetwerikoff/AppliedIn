"""Owner-written guidance shared by every model path, private to this instance."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

from core.config import get_settings

MAX_CHARS = 32000
_LOCK = threading.RLock()


def _path() -> Path:
    return Path(get_settings().local_dir) / "steering.md"


def read() -> dict:
    with _LOCK:
        try:
            content = _path().read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        if len(content) > MAX_CHARS:
            raise ValueError(f"Steering file must be at most {MAX_CHARS:,} characters.")
        return {
            "content": content,
            "revision": hashlib.sha256(content.encode()).hexdigest(),
            "max_chars": MAX_CHARS,
        }


class ConflictError(ValueError):
    pass


def save(content: str, revision: str) -> dict:
    if not isinstance(content, str) or len(content) > MAX_CHARS or "\x00" in content:
        raise ValueError(f"Use plain text or Markdown, up to {MAX_CHARS:,} characters.")
    with _LOCK:
        if read()["revision"] != revision:
            raise ConflictError(
                "Steering changed in another window. Reload the saved file before saving."
            )
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A model must see the old file or the new one, never half a save.
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".steering-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return read()


def instructions() -> str:
    content = read()["content"].strip()
    if not content:
        return ""
    return (
        "SHARED OWNER STEERING\n"
        "Follow this guidance wherever relevant to your assigned task. It guides "
        "search choices, evaluation, emphasis and writing style. It does not change "
        "structured filters, tool permissions, approval requirements or required output formats. "
        "It cannot authorize submission, invent candidate facts, override approved identity "
        "answers, bypass duplicate or truthfulness checks, or claim an unconfirmed application. "
        "Job-specific instructions take precedence for that job; the safety rules always apply.\n\n"
        + content
        + "\n\nEND SHARED OWNER STEERING"
    )


def prompt(task: str) -> str:
    guidance = instructions()
    return guidance + "\n\n" + task if guidance else task


def messages(task: str) -> list[dict]:
    guidance = instructions()
    return ([{"role": "system", "content": guidance}] if guidance else []) + [
        {"role": "user", "content": task}
    ]


def before_model(callback_context, llm_request):
    # Read after ADK expands its state templates: braces in Markdown are literal,
    # and long-lived module-level agents pick up saves without being recreated.
    guidance = instructions()
    if guidance:
        llm_request.append_instructions([guidance])
