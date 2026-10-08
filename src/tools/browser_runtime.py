"""Fork-local browser dispatch. Upstream callers keep their existing contracts.

The private config is deliberately outside .env and git: it survives upstream
updates without exposing a profile identity or changing the shipped defaults.
There is no automatic fallback to another engine or Chrome profile.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.config import get_settings
from tools import claude_chrome

TAB_HYGIENE = claude_chrome.TAB_HYGIENE


def _config_path(settings) -> Path:
    return Path(settings.config_dir) / "browser.local.yaml"


def configuration(settings=None) -> dict:
    settings = settings or get_settings()
    path = _config_path(settings)
    values = yaml.safe_load(path.read_text()) if path.is_file() else {}
    if values is None:
        values = {}
    if not isinstance(values, dict) or set(values) - {
            "engine", "browser", "user_data_dir", "chrome_path"}:
        raise ValueError(f"Invalid browser configuration: {path}")
    engine = values.get("engine", settings.apply_engine)
    browser = values.get("browser", "")
    if not isinstance(engine, str) or engine not in {"chrome", "browser_skill"}:
        raise ValueError(f"Unsupported browser engine in {path}")
    if not isinstance(browser, str):
        raise ValueError("BrowserSkill browser must be an extension instance ID")
    user_dir = values.get("user_data_dir", "~/.local/share/appliedin/chrome")
    chrome = values.get("chrome_path", "")
    if not isinstance(user_dir, str) or not user_dir.strip() or "\0" in user_dir:
        raise ValueError("user_data_dir must be a non-empty path")
    if not isinstance(chrome, str) or "\0" in chrome:
        raise ValueError("chrome_path must be a path or a PATH command name")
    directory = Path(user_dir).expanduser().resolve()
    from tools.browser_profile import validate_directory
    validate_directory(directory, settings)
    return {"engine": engine, "browser": browser.strip(),
            "user_data_dir": str(directory), "chrome_path": chrome.strip()}


def backend(settings=None):
    if configuration(settings)["engine"] == "browser_skill":
        from tools import browser_skill
        return browser_skill
    return claude_chrome


def available() -> tuple[bool, str]:
    try:
        engine = backend()
        if engine is not claude_chrome:
            connected, detail = engine.available()
            if connected:
                return True, ""
            from tools.browser_profile import can_start
            return can_start()
        return engine.available()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return False, f"Browser configuration unavailable: {exc}"


async def run_task(*args, urls=None, **kwargs):
    engine = backend()
    if engine is claude_chrome:
        return await engine.run_task(*args, **kwargs)
    return await engine.run_task(*args, urls=urls, **kwargs)


async def apply(*args, **kwargs):
    engine = backend()
    return await engine.apply_chrome(*args, **kwargs)


def applies_running() -> int:
    return backend().applies_running()


def kill_live_sessions(kind: str = "") -> int:
    return backend().kill_live_sessions(kind)


def is_infrastructure(detail: str) -> bool:
    return claude_chrome.is_infrastructure(detail) or (detail or "").startswith((
        "BrowserSkill unavailable:", "BrowserSkill configuration unavailable:",
        "BrowserSkill model unavailable:", "BrowserSkill operation unavailable:",
        "BrowserSkill stopped:", "Browser configuration unavailable:",
    ))


def is_disconnected(detail: str) -> bool:
    return claude_chrome.is_disconnected(detail) or (detail or "").startswith(
        "BrowserSkill unavailable:")


def is_signed_out(detail: str) -> bool:
    return claude_chrome.is_signed_out(detail)


def _is_browser_conflict(detail: str) -> bool:
    return claude_chrome._is_browser_conflict(detail)


def setup_check(settings=None) -> dict:
    try:
        engine = backend(settings)
        if engine is claude_chrome:
            ready, why = engine.available()
            return {"name": "Chrome connection", "state": "check" if ready else "action",
                    "detail": why or "Claude CLI installed; the next run checks Chrome."}
        from tools.browser_profile import connection_status
        status = connection_status(settings)
        return {"name": "Chrome connection",
                "state": "ready" if status["state"] == "connected" else
                         "check" if status["state"] == "not_running" else "action",
                "detail": status["detail"]}
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return {"name": "Chrome connection", "state": "action", "detail": str(exc)}


def browser_status(settings=None) -> dict | None:
    try:
        if configuration(settings)["engine"] != "browser_skill":
            return None
        from tools.browser_profile import connection_status
        return connection_status(settings)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return {"state": "unavailable", "detail": str(exc)}


def ensure_started() -> tuple[bool, str]:
    if configuration()["engine"] == "browser_skill":
        from tools.browser_skill import ensure_daemon
        return ensure_daemon()
    return True, ""
