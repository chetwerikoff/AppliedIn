"""Offline-testable, local-only recruiter reply metadata ingestion."""
from __future__ import annotations

import email
import email.policy
import email.utils
import json
import os
import re
import threading
import uuid
from pathlib import Path

from core.models import Status
from core.imap_readonly import IMAPReadError, ReadOnlyIMAP

WRITE_LOCK = threading.Lock()
STATE_NAME = "reply-ingest.json"


def enabled() -> bool:
    return os.environ.get("APPLIEDIN_IMAP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def poll_interval() -> int | None:
    raw = os.environ.get("APPLIEDIN_IMAP_POLL_INTERVAL_SEC", "")
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def local_credentials(settings, secrets=None) -> dict | None:
    if getattr(settings, "mode", "") != "local":
        return None
    if secrets is None:
        from core.stores import make_stores
        secrets = make_stores(settings).secrets
    try:
        value = secrets.get_json("imap")
        if not isinstance(value, dict) or not all(value.get(k) for k in ("host", "mailbox", "username", "password")):
            return None
        return value
    except Exception:
        return None


def _normalized(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _phrase(title: str, text: str) -> bool:
    t, hay = _normalized(title), _normalized(text)
    return bool(t and re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", hay))


def match_message(headers, candidates: list[dict], aliases: list[dict]) -> str:
    """Return one eligible pk or UNASSIGNED using only explicit recorded evidence."""
    msg = email.message_from_bytes(headers, policy=email.policy.default)
    subject = str(msg.get("subject", ""))
    sender = str(msg.get("from", ""))
    recipient_values = []
    for key in ("to", "cc", "delivered-to"):
        recipient_values.extend(email.utils.getaddresses(msg.get_all(key, [])))
    recipient_set = {address.casefold() for _, address in recipient_values if address}
    matched = [a for a in aliases if str(a.get("email", "")).casefold() in recipient_set]
    if matched:
        companies = {str(a.get("company", "")).casefold() for a in matched}
        signatures = {(str(a.get("email", "")).casefold(), str(a.get("id", "")),
                       str(a.get("company", "")).casefold(), tuple(sorted(map(str, a.get("used_pks") or []))))
                      for a in matched}
        matched_addresses = {str(a.get("email", "")).casefold() for a in matched}
        if len(companies) != 1 or len(signatures) != 1 or len(matched_addresses) != 1:
            return "UNASSIGNED"
        alias = matched[0]
        rows = [r for r in candidates if str(r.get("company", "")).casefold() == next(iter(companies))]
        alias_id = str(alias.get("id", ""))
        used_pks = set(map(str, alias.get("used_pks") or []))
        positives, contradictions = [], []
        for row in rows:
            profile = str(row.get("profile_id", ""))
            pk = str(row.get("pk", ""))
            other_alias = next((a for a in aliases if str(a.get("id", "")) == profile), None)
            positive = pk in used_pks or (alias_id and profile == alias_id)
            contradicted = bool(profile and other_alias is not None and profile != alias_id)
            contradicted = contradicted or bool(pk in used_pks and profile and profile != alias_id)
            if positive and not contradicted:
                positives.append(row)
            elif contradicted or (profile and (other_alias is not None or profile.startswith("rot-"))):
                contradictions.append(row)
        if positives:
            rows = positives
        elif contradictions:
            return "UNASSIGNED"
        if len(rows) == 1:
            return str(rows[0].get("pk") or "UNASSIGNED")
        title_matches = [r for r in rows if _phrase(str(r.get("title", "")), subject)]
        return str(title_matches[0].get("pk")) if len(title_matches) == 1 else "UNASSIGNED"

    display_name = email.utils.parseaddr(sender)[0]
    names = {str(r.get("company", "")).casefold(): str(r.get("company", "")) for r in candidates}
    identity = f"{display_name} {subject}"
    companies = [name for name in names.values() if _phrase(name, identity)]
    if len(set(companies)) != 1:
        return "UNASSIGNED"
    company = companies[0]
    rows = [r for r in candidates if str(r.get("company", "")).casefold() == company.casefold()
            and _phrase(str(r.get("title", "")), subject)]
    return str(rows[0].get("pk")) if len(rows) == 1 else "UNASSIGNED"


def _parse_headers(raw: bytes) -> dict:
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return {"date": "", "sender": "", "subject": "", "message_id": ""}
    def safe(name):
        try:
            return str(msg.get(name, ""))
        except Exception:
            return ""
    date, sender = safe("date"), safe("from")
    try:
        parsed = email.utils.parsedate_to_datetime(date) if date else None
        date = parsed.isoformat() if parsed else ""
    except Exception:
        date = ""
    address = email.utils.parseaddr(sender)[1] if sender else ""
    if sender and "@" not in address:
        sender = ""
    return {"date": date, "sender": sender, "subject": safe("subject"),
            "message_id": safe("message-id")}

def _state_path(settings) -> Path:
    return Path(settings.local_dir) / STATE_NAME


def _read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    os.replace(tmp, path)


def read_records(settings) -> list[dict]:
    with WRITE_LOCK:
        state = _read_state(_state_path(settings))
        rows = [row for account in state.get("accounts", {}).values()
                for row in account.get("records", [])]
        return [{k: row.get(k) for k in ("record_id", "date", "sender", "subject", "message_id", "label", "pk")}
                for row in rows]


def _account_key(creds: dict) -> str:
    return json.dumps([str(creds["host"]).casefold(), str(creds["username"]), str(creds["mailbox"])], separators=(",", ":"))


def ingest_pass(settings, credentials: dict | None = None, stores=None, transport=None,
                classifier=None) -> int:
    if settings.mode != "local":
        return 0
    creds = credentials or local_credentials(settings)
    if not creds:
        return 0
    if stores is None:
        from core.stores import make_stores
        stores = make_stores(settings)
    from core.rotation import aliases
    candidates = [*stores.tracking.query_status(Status.APPLIED),
                  *stores.tracking.query_status(Status.APPLIED_MANUAL)]
    alias_snapshot = aliases()
    client = transport or ReadOnlyIMAP(creds["host"], creds["username"], creds["password"], creds["mailbox"])
    state_path = _state_path(settings)
    key = _account_key(creds)
    processed_count = 0
    try:
        validity = str(getattr(client, "uidvalidity", "unknown"))
        with WRITE_LOCK:
            state = _read_state(state_path)
        account = state.get("accounts", {}).get(key, {})
        if account.get("uidvalidity") != validity:
            uids = sorted(client.search_uids("ALL"))[-200:]
            account.setdefault("processed", [])
            account.setdefault("message_ids", [])
            account.setdefault("records", [])
            account["uidvalidity"] = validity
            account["last_uid"] = 0
        else:
            cursor = int(account.get("last_uid", 0))
            uids = [u for u in client.search_uids(f"UID {cursor + 1}:*") if u > cursor]
        processed = set(map(str, account.get("processed", [])))
        mids = set(account.get("message_ids", []))
        for uid in sorted(uids):
            ident = f"{validity}:{uid}"
            if ident in processed:
                account["last_uid"] = max(int(account.get("last_uid", 0)), uid)
                state.setdefault("accounts", {})[key] = account
                with WRITE_LOCK:
                    _write_state(state_path, state)
                continue
            raw = client.fetch_headers(uid)
            fields = _parse_headers(raw)
            try:
                pk = match_message(raw, candidates, alias_snapshot)
            except Exception:
                pk = "UNASSIGNED"
            mid = fields["message_id"]
            if mid and mid in mids:
                processed.add(ident)
                account["processed"] = sorted(processed)
                account["last_uid"] = uid
                state.setdefault("accounts", {})[key] = account
                with WRITE_LOCK:
                    _write_state(state_path, state)
                continue
            text = client.fetch_text_prefix(uid)
            label = classifier(fields, text) if classifier else None
            record = {"record_id": str(uuid.uuid4()), **fields, "label": label, "pk": pk}
            account.setdefault("records", []).append(record)
            processed.add(ident)
            if mid:
                mids.add(mid)
            account["processed"] = sorted(processed)
            account["message_ids"] = sorted(mids)
            # Record and UID marker are atomic; cursor is deliberately committed next.
            with WRITE_LOCK:
                state.setdefault("accounts", {})[key] = account
                _write_state(state_path, state)
            account["last_uid"] = uid
            with WRITE_LOCK:
                state.setdefault("accounts", {})[key] = account
                _write_state(state_path, state)
            processed_count += 1
    finally:
        client.logout()
    return processed_count


def run_poller() -> None:
    import logging
    import time
    from core.config import get_settings
    from core.stores import make_stores
    logger = logging.getLogger(__name__)
    while True:
        try:
            settings = get_settings()
            creds = local_credentials(settings)
            if settings.mode == "local" and enabled() and creds:
                ingest_pass(settings, creds, make_stores(settings))
        except Exception:
            logger.warning("IMAP poll failed; will retry on next interval")
        time.sleep(poll_interval() or 60)


def secure_secrets_file(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise OSError("unsafe secrets file")
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    os.chmod(path, 0o600)
    if path.stat().st_mode & 0o077:
        raise OSError("secrets file permissions are not private")
