from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from core import reply_classifier, reply_ingest
from core.models import Status

LABELS = ["rejection", "interview_or_scheduling", "assessment",
          "recruiter_question", "auto_ack", "other"]


@pytest.mark.parametrize("label", LABELS)
def test_classifier_accepts_only_the_closed_vocabulary(label):
    assert reply_classifier.validated_label(f"  {label.upper()}  ") == label


@pytest.mark.parametrize("candidate", [None, 3, "unknown", "other, rejection", "<label>assessment</label>"])
def test_classifier_rejects_missing_malformed_and_injected_labels(candidate):
    assert reply_classifier.validated_label(candidate) == "other"


def test_model_call_is_tool_free_and_invalid_response_falls_back(monkeypatch):
    import litellm
    calls = []
    monkeypatch.setattr("core.config.get_settings", lambda: type("Settings", (), {
        "agent_model": lambda self, _: "provider/synthetic-model"})())
    monkeypatch.setattr("core.llm_access.missing_access", lambda _: [])
    class Message:
        content = "assessment extra"
    class Response:
        choices = [type("Choice", (), {"message": Message()})()]
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: (calls.append(kwargs), Response())[1])
    assert reply_classifier.classify("synthetic body") == "other"
    assert len(calls) == 1 and set(calls[0]) == {"model", "messages"}
    assert "synthetic body" in calls[0]["messages"][1]["content"]
def test_missing_model_access_skips_completion_without_disclosing_mail(monkeypatch):
    import litellm
    monkeypatch.setattr("core.config.get_settings", lambda: type("Settings", (), {
        "agent_model": lambda self, _: "openai/synthetic-model"})())
    monkeypatch.setattr("core.llm_access.missing_access", lambda _: ["unavailable"])
    monkeypatch.setattr(litellm, "completion", lambda **_: pytest.fail("provider must not be called"))
    assert reply_classifier.classify("private synthetic content") == "other"




class FakeTracking:
    def __init__(self, rows):
        self.rows = rows
        self.set_status_calls = []

    def query_status(self, status):
        return [row for row in self.rows if row["status"] == status.value]

    def all(self):
        return self.rows

    def get(self, pk):
        return next((row for row in self.rows if row["pk"] == pk), None)

    def set_status(self, pk, status, **fields):
        self.set_status_calls.append((pk, status, fields))
        row = self.get(pk)
        row.update(status=getattr(status, "value", status), **fields)


class FakeClient:
    uidvalidity = "31"

    def __init__(self, uids, headers, fragment):
        self.uids, self.headers, self.fragment = list(uids), headers, fragment
        self.header_fetches, self.body_fetches, self.closed = [], [], False

    def search_uids(self, query):
        if query == "ALL":
            return self.uids
        first = int(query.removeprefix("UID ").removesuffix(":*"))
        return [uid for uid in self.uids if uid >= first]

    def fetch_headers(self, uid):
        self.header_fetches.append(uid)
        return self.headers

    def fetch_text_prefix(self, uid):
        self.body_fetches.append(uid)
        return self.fragment[:4096]

    def logout(self):
        self.closed = True


def matched_headers(message_id=b""):
    return (b"From: example-co Recruiting <jobs@example-co.test>\r\n"
            b"Subject: Engineer\r\nX-Header-Sentinel: HEADER_SECRET\r\n"
            + message_id + b"Content-Type: multipart/mixed; boundary=reply-boundary\r\n\r\n")


def test_real_ingest_prefix_classifies_once_and_persists_only_safe_record(tmp_path, monkeypatch):
    row = {"pk": "example-co#role-1", "company": "example-co", "title": "Engineer",
           "status": "applied", "profile_id": ""}
    tracking = FakeTracking([row])
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    monkeypatch.setattr("core.rotation.aliases", lambda: [])
    captured = []
    monkeypatch.setattr(reply_classifier, "classify", lambda text: (captured.append(text), "interview_or_scheduling")[1])
    inline = "Visible inline body " + ("x" * 4100)
    fragment = ("--reply-boundary\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + inline
                + "\r\n--reply-boundary\r\nContent-Type: text/plain\r\n"
                + "Content-Disposition: attachment; filename=note.txt\r\n\r\nATTACHMENT_SECRET\r\n--reply-boundary--")
    client = FakeClient([1], matched_headers(), fragment.encode())
    assert reply_ingest.ingest_pass(settings, {"host": "imap.example-co.test", "username": "test",
        "mailbox": "INBOX", "password": "synthetic"}, type("Stores", (), {"tracking": tracking})(), client) == 1
    assert len(captured) == 1 and captured[0].startswith("Visible inline body")
    assert "HEADER_SECRET" not in captured[0] and "ATTACHMENT_SECRET" not in captured[0]
    assert len(captured[0].encode()) <= 4096
    records = reply_ingest.read_records(settings)
    assert records[0]["label"] == "interview_or_scheduling"
    assert records[0]["pk"] == row["pk"] and records[0]["record_id"]
    stored = (tmp_path / reply_ingest.STATE_NAME).read_text()
    assert "Visible inline body" not in stored and "HEADER_SECRET" not in stored
    assert "ATTACHMENT_SECRET" not in stored
    assert client.header_fetches == client.body_fetches == [1] and client.closed
    assert tracking.set_status_calls == []


def test_uid_and_message_id_replay_skip_body_and_model(tmp_path, monkeypatch):
    settings = type("Settings", (), {"mode": "local", "local_dir": str(tmp_path)})()
    credentials = {"host": "imap.example-co.test", "username": "test", "mailbox": "INBOX", "password": "synthetic"}
    headers = matched_headers(b"Message-ID: <stable@example-co.test>\r\n")
    monkeypatch.setattr("core.rotation.aliases", lambda: [])
    calls = []
    monkeypatch.setattr(reply_classifier, "classify", lambda text: (calls.append(text), "other")[1])
    tracking = FakeTracking([{ "pk": "example-co#role-1", "company": "example-co", "title": "Engineer", "status": "applied"}])
    stores = type("Stores", (), {"tracking": tracking})()
    first = FakeClient([1], headers, b"first body")
    assert reply_ingest.ingest_pass(settings, credentials, stores, first) == 1
    replay = FakeClient([1], headers, b"replayed body")
    assert reply_ingest.ingest_pass(settings, credentials, stores, replay) == 0
    duplicate = FakeClient([1, 2], headers, b"duplicate body")
    assert reply_ingest.ingest_pass(settings, credentials, stores, duplicate) == 0
    assert len(calls) == 1
    assert replay.body_fetches == []
    assert duplicate.body_fetches == []
    assert len(reply_ingest.read_records(settings)) == 1


def test_assignment_is_lock_protected_idempotent_and_conflicts_are_rejected(tmp_path):
    settings = type("Settings", (), {"local_dir": str(tmp_path)})()
    credentials = {"host": "imap.example-co.test", "username": "test", "mailbox": "INBOX"}
    account = reply_ingest._account_key(credentials)
    path = tmp_path / reply_ingest.STATE_NAME
    reply_ingest._write_state(path, {"accounts": {account: {"records": [
        {"record_id": "record-without-message-id", "message_id": "", "label": "assessment", "pk": "UNASSIGNED"}
    ]}}})
    assert reply_ingest.assign("record-without-message-id", "example-co#role-1", settings) == "assigned"
    assert reply_ingest.assign("record-without-message-id", "example-co#role-1", settings) == "assigned"
    assert reply_ingest.assign("record-without-message-id", "example-co#role-2", settings) == "conflict"
    assert reply_ingest.assign("missing", "example-co#role-2", settings) == "not_found"
    assert reply_ingest.read_records(settings)[0]["pk"] == "example-co#role-1"
def test_applications_projects_authoritative_reply_records_after_status_update(tmp_path, monkeypatch):
    import server
    from core.config import Settings

    row = {"pk": "example-co#role-1", "company": "example-co", "title": "Engineer",
           "status": "applied", "events": [], "outcome": "", "applied_at": "2026-09-30"}
    tracking = FakeTracking([row])
    stores = type("Stores", (), {"tracking": tracking,
        "artifacts": type("Artifacts", (), {"exists": lambda *_: False})()})()
    settings = Settings(mode="local", config_dir=str(tmp_path), local_dir=str(tmp_path))
    credentials = {"host": "imap.example-co.test", "username": "test", "mailbox": "INBOX", "password": "synthetic"}
    monkeypatch.setattr("core.rotation.aliases", lambda: [])
    labels = iter(["assessment", "recruiter_question"])
    monkeypatch.setattr(reply_classifier, "classify", lambda _: next(labels))
    client = FakeClient([1, 2], matched_headers(), b"synthetic reply body")
    assert reply_ingest.ingest_pass(settings, credentials, stores, client) == 2
    tracking.set_status(row["pk"], Status.APPLIED, outcome="offer", events=[{"kind": "applied", "at": "2026-09-30"}])
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "make_stores", lambda _: stores)
    monkeypatch.setattr(reply_ingest, "enabled", lambda: True)
    monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: credentials)
    payload = TestClient(server.create_app()).get("/applications").json()
    item = payload["items"][0]
    assert [reply["label"] for reply in item["replies"]] == ["assessment", "recruiter_question"]
    assert len({reply["record_id"] for reply in item["replies"]}) == 2
    assert item["status"] == "applied" and item["applied_at"] == "2026-09-30"
    assert row["outcome"] == "offer" and row["events"] == [{"kind": "applied", "at": "2026-09-30"}]
    assert "synthetic reply body" not in json.dumps(payload)
    assert len(tracking.set_status_calls) == 1




def test_applications_and_unassigned_api_survive_corrupt_reply_state(tmp_path, monkeypatch):
    import server
    from core.config import Settings
    settings = Settings(mode="local", config_dir=str(tmp_path), local_dir=str(tmp_path))
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "make_stores", lambda _: type("Stores", (), {
        "tracking": FakeTracking([]), "artifacts": type("Artifacts", (), {"exists": lambda *_: False})()})())
    monkeypatch.setattr(reply_ingest, "enabled", lambda: True)
    monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: {"host": "imap.example-co.test"})
    monkeypatch.setattr(reply_ingest, "read_records", lambda *_: (_ for _ in ()).throw(ValueError("private-path-and-secret")))
    client = TestClient(server.create_app())
    apps = client.get("/applications").json()
    unassigned = client.get("/replies/unassigned").json()
    assert apps["items"] == [] and apps["replies"] == []
    assert apps["error"] == "Recruiter replies are temporarily unavailable."
    assert unassigned == {"replies": [], "error": "Recruiter replies are temporarily unavailable."}
    assert "private-path-and-secret" not in json.dumps(apps) + json.dumps(unassigned)


def test_assignment_api_validates_application_and_uses_stored_record(tmp_path, monkeypatch):
    import server
    from core.config import Settings
    settings = Settings(mode="local", config_dir=str(tmp_path), local_dir=str(tmp_path))
    rows = [
        {"pk": "example-co#role-1", "status": "applied", "company": "example-co"},
        {"pk": "example-co#role-2", "status": "applied_manual", "company": "example-co"},
        {"pk": "example-co#tailored", "status": "tailored", "company": "example-co"},
        {"pk": "meta#run#example-co", "status": "applied", "company": "example-co"},
    ]
    tracking = FakeTracking(rows)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "make_stores", lambda _: type("Stores", (), {"tracking": tracking})())
    monkeypatch.setattr(reply_ingest, "enabled", lambda: True)
    monkeypatch.setattr(reply_ingest, "local_credentials", lambda *_: {"host": "imap.example-co.test"})
    account = reply_ingest._account_key({"host": "imap.example-co.test", "username": "test", "mailbox": "INBOX"})
    path = tmp_path / reply_ingest.STATE_NAME
    reply_ingest._write_state(path, {"accounts": {account: {"records": [
        {"record_id": "r1", "date": "2026-10-01", "sender": "recruiter@example-co.test",
         "subject": "Stored subject", "label": "assessment", "pk": "UNASSIGNED"}
    ]}}})
    client = TestClient(server.create_app())
    assert client.post("/replies/r1/assign", json={"pk": rows[0]["pk"], "label": "forged", "subject": "forged"}).json() == {"ok": True}
    assert reply_ingest.read_records(settings)[0]["subject"] == "Stored subject"
    assert reply_ingest.assign("r1", rows[0]["pk"], settings) == "assigned"
    assert client.post("/replies/r1/assign", json={"pk": rows[1]["pk"]}).status_code == 409
    for pk in ("", rows[2]["pk"], rows[3]["pk"]):
        response = client.post("/replies/r1/assign", json={"pk": pk})
        assert response.json()["ok"] is False
    assert client.post("/replies/unknown/assign", json={"pk": rows[0]["pk"]}).status_code == 404
    assert tracking.set_status_calls == []
    assert [row["status"] for row in rows] == ["applied", "applied_manual", "tailored", "applied"]
