from __future__ import annotations

import email
import email.policy
import json
from pathlib import Path

from core import imap_readonly, reply_ingest


def header(**items):
    msg = email.message.EmailMessage(policy=email.policy.default)
    for key, value in items.items():
        msg[key.replace("_", "-")] = value
    return msg.as_bytes()


def test_matcher_prefers_recorded_alias_usage_and_rejects_conflict():
    rows = [
        {"pk": "example-co#one", "company": "example-co", "title": "Backend Engineer", "status": "applied", "profile_id": "rot-new"},
        {"pk": "example-co#two", "company": "example-co", "title": "Platform Engineer", "status": "applied_manual", "profile_id": "rot-other"},
    ]
    aliases = [{"id": "rot-new", "email": "alias+new@example-co.test", "company": "example-co", "used_pks": ["example-co#one"]},
               {"id": "rot-other", "email": "alias-old@example-co.test", "company": "example-co", "used_pks": ["example-co#two"]}]
    assert reply_ingest.match_message(header(To="Alias+New@example-co.test", Subject="Reply"), rows, aliases) == "example-co#one"
    unused = dict(aliases[0], id="rot-unused", email="alias-unused@example-co.test", used_pks=[])
    assert reply_ingest.match_message(header(To="alias-unused@example-co.test", Subject="Reply"), rows, [unused]) == "UNASSIGNED"


def test_matcher_fallback_requires_explicit_company_and_subject_title():
    rows = [{"pk": "example-co#one", "company": "example-co", "title": "Backend Engineer"}]
    assert reply_ingest.match_message(header(From="Example-co Recruiting <jobs@shared-ats.test>",
                                             Subject="Backend Engineer"), rows, []) == "example-co#one"
    assert reply_ingest.match_message(header(From="Recruiting <jobs@shared-ats.test>",
                                             Subject="Backend Engineer"), rows, []) == "UNASSIGNED"
    encoded = b"From: Recruiting <jobs@shared-ats.test>\r\nSubject: =?UTF-8?Q?Example-co_Backend_Engineer?=\r\n\r\n"
    assert reply_ingest.match_message(encoded, rows, []) == "example-co#one"


def test_imap_wrapper_sets_finite_timeout_and_only_safe_fetches(monkeypatch):
    calls = []

    class Fake:
        def login(self, *_): calls.append(("login",)); return "OK", []
        def select(self, mailbox, readonly=False): calls.append(("select", mailbox, readonly)); return "OK", [b"1"]
        def uid(self, *args):
            calls.append(args)
            if args[0] == "SEARCH": return "OK", [b"1"]
            return "OK", [(b"x", b"safe")]
        def logout(self): calls.append(("logout",)); return "BYE", []

    def constructor(host, **kwargs):
        calls.append(("constructor", host, kwargs))
        return Fake()
    monkeypatch.setattr(imap_readonly.imaplib, "IMAP4_SSL", constructor)
    client = imap_readonly.ReadOnlyIMAP("imap.example-co.test", "test", "synthetic", "INBOX")
    assert client.search_uids() == [1]
    assert client.fetch_headers(1) == b"safe"
    assert client.fetch_text_prefix(1) == b"safe"
    client.logout()
    kwargs = next(call[2] for call in calls if call[0] == "constructor")
    assert kwargs["timeout"] == imap_readonly.TIMEOUT_SECONDS > 0
    assert kwargs["ssl_context"].check_hostname
    assert ("select", "INBOX", True) in calls
    assert any("BODY.PEEK[HEADER]" in args[-1] for args in calls if args[0] == "FETCH")
    assert any("BODY.PEEK[TEXT]<0.4096>" in args[-1] for args in calls if args[0] == "FETCH")


def test_secrets_file_is_restricted(tmp_path):
    path = tmp_path / "secrets.json"
    reply_ingest.secure_secrets_file(path)
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    reply_ingest.secure_secrets_file(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_source_has_only_the_readonly_imap_import():
    root = Path(__file__).parents[1] / "src"
    imap_imports, smtp_imports = [], []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if "import imaplib" in text or "from imaplib" in text:
            imap_imports.append(path.relative_to(root).as_posix())
        if "import smtplib" in text or "from smtplib" in text:
            smtp_imports.append(path.relative_to(root).as_posix())
    assert imap_imports == ["core/imap_readonly.py"]
    assert smtp_imports == []


def test_cloud_mode_never_constructs_transport():
    class Settings:
        mode = "cloud"
        local_dir = "."
    assert reply_ingest.ingest_pass(Settings(), credentials={"host": "example", "username": "x", "password": "y", "mailbox": "INBOX"}) == 0

def test_processed_uid_replay_advances_cursor_without_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.rotation.aliases", lambda: [])
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    credentials = {"host": "imap.example-co.test", "username": "test", "password": "synthetic", "mailbox": "INBOX"}
    key = reply_ingest._account_key(credentials)
    path = tmp_path / reply_ingest.STATE_NAME
    prior = {"record_id": "record-1", "date": "", "sender": "", "subject": "", "message_id": "", "label": None, "pk": "UNASSIGNED"}
    path.write_text(json.dumps({"accounts": {key: {"uidvalidity": "7", "last_uid": 40, "processed": ["7:41"], "message_ids": [], "records": [prior]}}}))

    class Tracking:
        def query_status(self, status): return []
        def all(self): raise AssertionError("full tracking scan is forbidden")
    class Client:
        uidvalidity = "7"
        def search_uids(self, query): return [41]
        def fetch_headers(self, uid): raise AssertionError("processed UID must not be fetched")
        def fetch_text_prefix(self, uid): raise AssertionError("processed UID has no body fetch")
        def logout(self): pass
    stores = type("Stores", (), {"tracking": Tracking()})()
    assert reply_ingest.ingest_pass(settings, credentials, stores, Client()) == 0
    state = json.loads(path.read_text())["accounts"][key]
    assert state["last_uid"] == 41
    assert len(reply_ingest.read_records(settings)) == 1

def test_bad_header_is_recorded_unassigned_and_does_not_block_later_uids(tmp_path, monkeypatch):
    aliases_calls = []
    monkeypatch.setattr("core.rotation.aliases", lambda: aliases_calls.append(1) or [])
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    credentials = {"host": "imap.example-co.test", "username": "test", "password": "synthetic", "mailbox": "INBOX"}
    candidates_read = []
    class Tracking:
        def query_status(self, status):
            candidates_read.append(status)
            return []
        def all(self): raise AssertionError("tracking.all() must not be used")
    messages = {1: b"From: recruiter@example-co.test\r\nSubject: One\r\nMessage-ID: <one@example-co.test>\r\n\r\n",
                2: b"From: %%%\r\nSubject: broken\r\n\r\n",
                3: b"From: recruiter@example-co.test\r\nSubject: Three\r\nMessage-ID: <three@example-co.test>\r\n\r\n"}
    class Client:
        uidvalidity = "99"
        def search_uids(self, query): return [1, 2, 3]
        def fetch_headers(self, uid): return messages[uid]
        def fetch_text_prefix(self, uid): return b"body must remain in memory"
        def logout(self): pass
    stores = type("Stores", (), {"tracking": Tracking()})()
    assert reply_ingest.ingest_pass(settings, credentials, stores, Client()) == 3
    records = reply_ingest.read_records(settings)
    assert len(records) == 3
    assert records[1]["sender"] == "" and records[1]["pk"] == "UNASSIGNED"
    assert all("body" not in record for record in records)
    assert len(candidates_read) == 2 and len(aliases_calls) == 1
