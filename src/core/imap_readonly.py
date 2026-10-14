"""The only network boundary for recruiter-reply IMAP reads."""
from __future__ import annotations

import imaplib
import re
import ssl

TIMEOUT_SECONDS = 15
TEXT_PREFIX_BYTES = 4096


class IMAPReadError(RuntimeError):
    """Sanitized transport failure; never includes server or credential text."""


class ReadOnlyIMAP:
    def __init__(self, host: str, username: str, password: str, mailbox: str):
        self._client = None
        try:
            self._client = imaplib.IMAP4_SSL(
                host, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS)
            self._client.login(username, password)
            status, _ = self._client.select(mailbox, readonly=True)
            if status != "OK":
                raise IMAPReadError("IMAP mailbox unavailable")
            response = self._client.response("UIDVALIDITY")[1] if hasattr(self._client, "response") else None
            value = response[0] if response else b"unknown"
            self.uidvalidity = value.decode(errors="ignore") if isinstance(value, bytes) else str(value)
        except Exception:
            self.logout()
            raise IMAPReadError("IMAP connection or authentication failed") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.logout()

    def search_uids(self, query: str = "ALL") -> list[int]:
        if query != "ALL" and not re.fullmatch(r"UID [1-9][0-9]*:\*", query):
            raise ValueError("unsupported IMAP search")
        try:
            status, data = self._client.uid("SEARCH", None, query)
            if status != "OK":
                raise IMAPReadError("IMAP search failed")
            return [int(uid) for uid in (data[0] or b"").split()]
        except Exception:
            raise IMAPReadError("IMAP search failed") from None

    def fetch_headers(self, uid: int) -> bytes:
        return self._fetch(uid, "BODY.PEEK[HEADER]")

    def fetch_text_prefix(self, uid: int) -> bytes:
        return self._fetch(uid, f"BODY.PEEK[TEXT]<0.{TEXT_PREFIX_BYTES}>")

    def _fetch(self, uid: int, section: str) -> bytes:
        try:
            status, data = self._client.uid("FETCH", str(uid), f"({section})")
            if status != "OK":
                raise IMAPReadError("IMAP fetch failed")
            for item in data:
                if isinstance(item, tuple) and isinstance(item[1], bytes):
                    return item[1]
            return b""
        except Exception:
            raise IMAPReadError("IMAP fetch failed") from None

    def logout(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                client.logout()
            except Exception:
                pass
