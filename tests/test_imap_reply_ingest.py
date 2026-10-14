from __future__ import annotations

import email
import json
import socket
import threading
from pathlib import Path

import pytest

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


def test_cloud_mode_never_constructs_transport_or_secrets_backend(monkeypatch):
    from core import stores
    class Settings:
        mode = "cloud"
        local_dir = "."
    monkeypatch.setattr(reply_ingest, "ReadOnlyIMAP", lambda *_: pytest.fail("cloud opened IMAP"))
    monkeypatch.setattr(stores, "make_stores", lambda *_: pytest.fail("cloud constructed secrets backend"))
    assert reply_ingest.local_credentials(Settings()) is None
    assert reply_ingest.ingest_pass(Settings(), credentials=fake_credentials()) == 0

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
    assert b"body must remain in memory" not in (tmp_path / reply_ingest.STATE_NAME).read_bytes()
    assert len(candidates_read) == 2 and len(aliases_calls) == 1


class FakeTracking:
    def __init__(self, applied=None, manual=None):
        self.applied = applied or []
        self.manual = manual or []
        self.queries = []

    def query_status(self, status):
        self.queries.append(status)
        return self.applied if status.value == "applied" else self.manual

    def all(self):
        raise AssertionError("tracking.all() is outside the candidate snapshot contract")


class FakeClient:
    def __init__(self, validity="1", uids=(), messages=None):
        self.uidvalidity = validity
        self.uids = list(uids)
        self.messages = messages or {}
        self.fetched = []
        self.queries = []

    def search_uids(self, query):
        self.queries.append(query)
        if query == "ALL":
            return list(self.uids)
        start = int(query.removeprefix("UID ").removesuffix(":*"))
        return [uid for uid in self.uids if uid >= start]

    def fetch_headers(self, uid):
        self.fetched.append(uid)
        return self.messages.get(uid, b"From: recruiter@example-co.test\r\nSubject: reply\r\n\r\n")

    def fetch_text_prefix(self, uid):
        return b"synthetic body prefix"

    def logout(self):
        pass


def fake_stores(tracking=None):
    return type("Stores", (), {"tracking": tracking or FakeTracking()})()


def fake_credentials(host="imap.example-co.test", username="test"):
    return {"host": host, "mailbox": "INBOX", "username": username, "password": "synthetic-secret"}


def run_fake_ingest(tmp_path, monkeypatch, client, credentials=None, tracking=None, classifier=None):
    monkeypatch.setattr("core.rotation.aliases", lambda: [])
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    inserted = reply_ingest.ingest_pass(settings, credentials or fake_credentials(),
                                        fake_stores(tracking), client, classifier)
    return settings, inserted


def test_positive_alias_evidence_with_no_eligible_positive_fails_closed():
    alias = {"id": "rot-a", "email": "reply@example-co.test", "company": "example-co",
             "used_pks": ["example-co#old"]}
    only_new = {"pk": "example-co#new", "company": "example-co", "title": "Backend Engineer",
                "status": "applied", "profile_id": ""}
    assert reply_ingest.match_message(
        header(To="reply@example-co.test", Subject="Backend Engineer"), [only_new], [alias]) == "UNASSIGNED"


def test_matcher_handles_cc_delivered_to_retired_alias_and_competing_recipients():
    row = {"pk": "example-co#one", "company": "example-co", "title": "Backend Engineer",
           "status": "applied", "profile_id": "rot-retired"}
    retired = {"id": "rot-retired", "email": "old@example-co.test", "company": "example-co",
               "used_pks": [row["pk"]], "retired": True}
    for recipient in ("Cc", "Delivered-To"):
        assert reply_ingest.match_message(header(**{recipient: retired["email"]}), [row], [retired]) == row["pk"]
    other = {"id": "rot-other", "email": "other@example-co.test", "company": "example-co", "used_pks": []}
    assert reply_ingest.match_message(
        header(To=f"{retired['email']}, {other['email']}"), [row], [retired, other]) == "UNASSIGNED"


def test_multiple_alias_used_pks_need_unique_subject_title():
    alias = {"id": "rot-a", "email": "reply@example-co.test", "company": "example-co",
             "used_pks": ["example-co#one", "example-co#two"]}
    rows = [{"pk": "example-co#one", "company": "example-co", "title": "Backend Engineer",
             "profile_id": "rot-a"},
            {"pk": "example-co#two", "company": "example-co", "title": "Platform Engineer",
             "profile_id": "rot-a"}]
    assert reply_ingest.match_message(header(To=alias["email"], Subject="Platform Engineer"), rows, [alias]) == "example-co#two"
    assert reply_ingest.match_message(header(To=alias["email"], Subject="reply"), rows, [alias]) == "UNASSIGNED"


def test_shared_ats_and_url_host_do_not_prove_sender_company():
    row = {"pk": "example-co#one", "company": "example-co", "title": "Backend Engineer",
           "jd_url": "https://jobs.shared-ats.test/example-co/123", "ats": "greenhouse"}
    assert reply_ingest.match_message(
        header(From="Recruiting <jobs@shared-ats.test>", Subject="Backend Engineer"), [row], []) == "UNASSIGNED"


def test_wrapper_refuses_generic_and_mutating_commands():
    class Fake:
        def login(self, *_): return "OK", []
        def select(self, *_args, **_kwargs): return "OK", []
        def response(self, *_): return "UIDVALIDITY", [b"3"]
        def logout(self): return "BYE", []
    original = imap_readonly.imaplib.IMAP4_SSL
    imap_readonly.imaplib.IMAP4_SSL = lambda *_args, **_kwargs: Fake()
    try:
        client = imap_readonly.ReadOnlyIMAP("imap.example-co.test", "test", "synthetic", "INBOX")
        assert not hasattr(client, "uid") and not hasattr(client, "command")
        with pytest.raises(ValueError):
            client.search_uids("UID 1:* STORE 1 +FLAGS \\Seen")
        client.logout()
    finally:
        imap_readonly.imaplib.IMAP4_SSL = original


def test_two_inbox_accounts_with_same_uidvalidity_and_uid_are_isolated(tmp_path, monkeypatch):
    for host, username in (("imap-one.example-co.test", "account-one"), ("imap-two.example-co.test", "account-two")):
        client = FakeClient("77", [9], {9: b"From: recruiter@example-co.test\r\nMessage-ID: <same@example-co.test>\r\n\r\n"})
        settings, inserted = run_fake_ingest(tmp_path, monkeypatch, client, fake_credentials(host, username))
        assert inserted == 1
    state = json.loads((tmp_path / reply_ingest.STATE_NAME).read_text())
    assert len(state["accounts"]) == 2
    assert len(reply_ingest.read_records(settings)) == 2


def test_new_uid_with_duplicate_message_id_does_not_create_record(tmp_path, monkeypatch):
    credentials = fake_credentials()
    one = FakeClient("78", [1], {1: b"Message-ID: <same@example-co.test>\r\n\r\n"})
    settings, inserted = run_fake_ingest(tmp_path, monkeypatch, one, credentials)
    assert inserted == 1
    two = FakeClient("78", [1, 2], {2: b"Message-ID: <same@example-co.test>\r\n\r\n"})
    _, inserted = run_fake_ingest(tmp_path, monkeypatch, two, credentials)
    assert inserted == 0
    account = json.loads((tmp_path / reply_ingest.STATE_NAME).read_text())["accounts"][reply_ingest._account_key(credentials)]
    assert len(account["records"]) == 1 and "78:2" in account["processed"] and account["last_uid"] == 2


def test_uidvalidity_reset_scans_only_newest_200_and_retains_known_message_id(tmp_path, monkeypatch):
    credentials = fake_credentials()
    first = FakeClient("10", [1], {1: b"Message-ID: <known@example-co.test>\r\n\r\n"})
    settings, _ = run_fake_ingest(tmp_path, monkeypatch, first, credentials)
    second = FakeClient("11", range(1, 351),
                       {uid: b"Message-ID: <known@example-co.test>\r\n\r\n" for uid in range(151, 351)})
    _, inserted = run_fake_ingest(tmp_path, monkeypatch, second, credentials)
    assert inserted == 0 and second.fetched == list(range(151, 351))
    account = json.loads((tmp_path / reply_ingest.STATE_NAME).read_text())["accounts"][reply_ingest._account_key(credentials)]
    assert account["last_uid"] == 350 and len(account["records"]) == 1


def test_storage_failure_keeps_cursor_and_next_pass_retries(tmp_path, monkeypatch):
    client = FakeClient("12", [41], {41: b"From: recruiter@example-co.test\r\n\r\n"})
    original = reply_ingest._write_state
    writes = 0
    def fail_second_write(path, state):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic disk failure")
        original(path, state)
    monkeypatch.setattr(reply_ingest, "_write_state", fail_second_write)
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    with pytest.raises(OSError, match="synthetic disk failure"):
        reply_ingest.ingest_pass(settings, fake_credentials(), fake_stores(), client)
    path = tmp_path / reply_ingest.STATE_NAME
    state = json.loads(path.read_text())["accounts"][reply_ingest._account_key(fake_credentials())]
    assert state["last_uid"] == 0 and state["processed"] == []
    monkeypatch.setattr(reply_ingest, "_write_state", original)
    retry = FakeClient("12", [41], {41: b"From: recruiter@example-co.test\r\n\r\n"})
    assert reply_ingest.ingest_pass(settings, fake_credentials(), fake_stores(), retry) == 1
    state = json.loads(path.read_text())["accounts"][reply_ingest._account_key(fake_credentials())]
    assert state["last_uid"] == 41 and len(state["records"]) == 1


def test_socket_open_is_forbidden_during_fake_ingest(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline test attempted to open a socket")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    client = FakeClient("13", [1], {1: b"Subject: offline\r\n\r\n"})
    _, inserted = run_fake_ingest(tmp_path, monkeypatch, client)
    assert inserted == 1


def test_daemon_poller_registration_gates_all_configuration(monkeypatch):
    import daemon
    from core import reply_ingest
    started = []
    class Thread:
        def __init__(self, *_, name="", **__): self.name = name
        def start(self): started.append(self.name)
    monkeypatch.setattr(daemon.threading, "Thread", Thread)
    monkeypatch.setattr(reply_ingest, "enabled", lambda: True)
    monkeypatch.setattr(reply_ingest, "poll_interval", lambda: 30)
    monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: {"host": "x"})
    stores = type("Stores", (), {"secrets": object()})()
    local = type("Settings", (), {"mode": "local"})()
    cloud = type("Settings", (), {"mode": "cloud"})()
    assert daemon._start_imap_poller(stores, local) is True
    assert started == ["imap-replies"]
    for setting, enabled, interval, credentials in (
            (local, False, 30, {"host": "x"}), (local, True, None, {"host": "x"}),
            (local, True, 30, None), (cloud, True, 30, {"host": "x"})):
        monkeypatch.setattr(reply_ingest, "enabled", lambda value=enabled: value)
        monkeypatch.setattr(reply_ingest, "poll_interval", lambda value=interval: value)
        monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_, value=credentials: value)
        assert daemon._start_imap_poller(stores, setting) is False
    assert started == ["imap-replies"]


def test_poller_uses_configured_cadence_and_survives_pass_failure(monkeypatch, caplog):
    import logging
    from core import config, stores
    monkeypatch.setenv("APPLIEDIN_IMAP_ENABLED", "1")
    monkeypatch.setenv("APPLIEDIN_IMAP_POLL_INTERVAL_SEC", "23")
    settings = type("Settings", (), {"mode": "local"})()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(stores, "make_stores", lambda *_: object())
    monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: fake_credentials())
    monkeypatch.setattr(reply_ingest, "ingest_pass", lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic-secret")))
    waiting, release = threading.Event(), threading.Event()
    class StopEvent:
        def is_set(self): return False
        def wait(self, interval):
            assert interval == 23
            waiting.set()
            release.wait(3)
            return True
    thread = threading.Thread(target=reply_ingest.run_poller, args=(StopEvent(),), daemon=True)
    with caplog.at_level(logging.WARNING, logger="core.reply_ingest"):
        thread.start()
        assert waiting.wait(3)
        assert thread.is_alive()
        release.set()
        thread.join(3)
    assert not thread.is_alive() and "synthetic-secret" not in caplog.text
    assert "IMAP poll failed; will retry on next interval" in caplog.text


def test_imap_setup_cloud_refuses_before_prompt_and_store(monkeypatch):
    import builtins
    import getpass
    from core import config, stores
    from cli import imap_setup
    cloud = type("Settings", (), {"mode": "cloud", "local_dir": "."})()
    monkeypatch.setattr(config, "get_settings", lambda: cloud)
    monkeypatch.setattr(builtins, "input", lambda *_: pytest.fail("cloud setup prompted"))
    monkeypatch.setattr(getpass, "getpass", lambda *_: pytest.fail("cloud setup prompted for password"))
    monkeypatch.setattr(stores, "make_stores", lambda *_: pytest.fail("cloud setup built a backend"))
    assert imap_setup()["status"] == "refused"


def test_setup_health_disabled_cloud_timeout_and_success(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import server
    from core import reply_ingest
    from core.config import Settings
    credentials = fake_credentials()
    def request(mode, is_enabled, creds, transport_error=None):
        settings = Settings(mode=mode, config_dir=str(tmp_path / "config"),
                            local_dir=str(tmp_path))
        monkeypatch.setattr(server, "get_settings", lambda: settings)
        monkeypatch.setattr(reply_ingest, "enabled", lambda: is_enabled)
        monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: creds if mode == "local" else None)
        class Transport:
            def __init__(self, *_):
                if transport_error: raise transport_error
            def __enter__(self): return self
            def __exit__(self, *_): pass
        monkeypatch.setattr("core.imap_readonly.ReadOnlyIMAP", Transport)
        return TestClient(server.create_app()).get("/setup-health").json()["checks"]
    disabled = request("local", False, credentials)
    assert next(c for c in disabled if c["name"] == "IMAP replies")["state"] == "check"
    cloud = request("cloud", True, credentials)
    assert next(c for c in cloud if c["name"] == "IMAP replies")["state"] == "check"
    timeout = request("local", True, credentials, TimeoutError("synthetic-secret"))
    timeout_item = next(c for c in timeout if c["name"] == "IMAP replies")
    assert timeout_item["state"] == "action" and "synthetic-secret" not in str(timeout)
    success = request("local", True, credentials)
    assert next(c for c in success if c["name"] == "IMAP replies")["state"] == "ready"
    assert all(any(c["name"] == name for c in timeout) for name in ("Résumé", "Model access", "PDF rendering"))


def test_classifier_failure_records_null_label_and_advances(tmp_path, monkeypatch):
    client = FakeClient("14", [1], {1: b"Subject: test\r\n\r\n"})
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    tracking = FakeTracking()
    inserted = reply_ingest.ingest_pass(settings, fake_credentials(), fake_stores(tracking), client,
                                       lambda *_: (_ for _ in ()).throw(RuntimeError("classifier")))
    assert inserted == 1
    assert reply_ingest.read_records(settings)[0]["label"] is None
    account = json.loads((tmp_path / reply_ingest.STATE_NAME).read_text())["accounts"][reply_ingest._account_key(fake_credentials())]
    assert account["last_uid"] == 1

def test_opt_in_and_interval_parsers_fail_closed(monkeypatch):
    monkeypatch.delenv("APPLIEDIN_IMAP_ENABLED", raising=False)
    monkeypatch.delenv("APPLIEDIN_IMAP_POLL_INTERVAL_SEC", raising=False)
    assert not reply_ingest.enabled() and reply_ingest.poll_interval() is None
    monkeypatch.setenv("APPLIEDIN_IMAP_ENABLED", "true")
    assert reply_ingest.enabled()
    for value in ("invalid", "0", "-5"):
        monkeypatch.setenv("APPLIEDIN_IMAP_POLL_INTERVAL_SEC", value)
        assert reply_ingest.poll_interval() is None
    monkeypatch.setenv("APPLIEDIN_IMAP_POLL_INTERVAL_SEC", "12")
    assert reply_ingest.poll_interval() == 12


def test_state_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    calls = []
    original = reply_ingest.os.fsync
    monkeypatch.setattr(reply_ingest.os, "fsync", lambda fd: (calls.append(fd), original(fd))[1])
    path = tmp_path / reply_ingest.STATE_NAME
    reply_ingest._write_state(path, {"accounts": {}})
    assert len(calls) == 2


def test_uid_commit_holds_lock_across_state_reread_and_write(tmp_path, monkeypatch):
    original_read, original_write = reply_ingest._read_state, reply_ingest._write_state
    def locked_read(path):
        assert reply_ingest.WRITE_LOCK.locked()
        return original_read(path)
    def locked_write(path, state):
        assert reply_ingest.WRITE_LOCK.locked()
        return original_write(path, state)
    monkeypatch.setattr(reply_ingest, "_read_state", locked_read)
    monkeypatch.setattr(reply_ingest, "_write_state", locked_write)
    key = reply_ingest._account_key(fake_credentials())
    record = {"record_id": "record-1", "date": "", "sender": "", "subject": "",
              "message_id": "", "label": None, "pk": "UNASSIGNED"}
    assert reply_ingest._commit_uid(tmp_path / reply_ingest.STATE_NAME, key, "15", 1, record, "")
