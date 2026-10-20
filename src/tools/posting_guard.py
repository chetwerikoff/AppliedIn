"""Deterministic, source-aware boundary for untrusted job-posting text.

A model may *read* a hostile web page before the Python browser reader returns;
this guard covers every subsequent application-controlled model input. No model,
network or remote CSS renderer is involved here.
"""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

REVISION = "posting-guard-v1"
_MESSAGES = {
    "hidden_html": "Hidden page content removed",
    "hidden_link": "Hidden posting link removed",
    "hidden_url": "Hidden URL removed",
    "same_color": "Indistinguishable foreground and background removed",
    "zero_width": "Invisible Unicode controls removed",
    "bidi_control": "Bidirectional Unicode controls removed",
    "mixed_script": "Mixed-script token in the posting",
    "model_instruction": "Instruction directed at an automated reader removed",
    "source_html_unavailable": "Original posting HTML not yet inspected",
    "guard_error": "Posting safety inspection failed",
}
_POSITIVE = frozenset(_MESSAGES) - {"source_html_unavailable"}
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff]")
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
_URL = re.compile(r"https?://[^\s<>'\"]+", re.I)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_INSTRUCTION = re.compile(
    r"(?i)(?:\bignore (?:all |any |the )?(?:previous|prior|above) instructions\b|"
    r"\bif you are (?:an? )?(?:ai|llm|language model|assistant|bot)\b|"
    r"\b(?:system|developer) (?:prompt|message|instructions?)\s*:|"
    r"\b(?:assistant|chatgpt|claude|gpt)\s*:\s*(?:ignore|output|say|write|follow)\b|"
    r"\b(?:disregard|override) (?:your|the) (?:previous |system |developer )?instructions\b)"
)
_BLOCK = frozenset(
    "address article aside blockquote br dd div dl dt footer form h1 h2 h3 h4 h5 h6 "
    "header hr li main nav ol p pre section table td th tr ul".split()
)
_DROP = frozenset("script style noscript template iframe object svg head".split())


def _style_hidden(raw: str, inherited_fg: str = "", inherited_bg: str = ""
                  ) -> tuple[bool, bool, str, str]:
    # Inline !important is a priority marker, not part of the CSS value.
    # Respect it when the same property is declared more than once.
    props: dict[str, tuple[str, bool]] = {}
    for part in raw.split(";"):
        key, sep, value = part.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip().lower()
        important = bool(re.search(r"\s*!important\s*$", value))
        value = re.sub(r"\s+", "", re.sub(r"\s*!important\s*$", "", value))
        if key not in props or important or not props[key][1]:
            props[key] = (value, important)

    def prop(name: str, fallback: str = "") -> str:
        return props[name][0] if name in props else fallback

    hidden = (prop("display") == "none"
              or prop("visibility") in {"hidden", "collapse"}
              or prop("opacity") in {"0", "0.0", "0%"})
    fg = prop("color", inherited_fg)
    bg = prop("background-color", prop("background", inherited_bg))
    if fg in {"inherit", "unset"}:
        fg = inherited_fg
    if bg in {"transparent", "inherit", "unset"}:
        bg = inherited_bg

    def color(value: str) -> str:
        white = {"white", "#fff", "#ffffff", "rgb(255,255,255)"}
        black = {"black", "#000", "#000000", "rgb(0,0,0)"}
        if value in white:
            return "#ffffff"
        if value in black:
            return "#000000"
        # Deterministic inline literals only; no computed stylesheet claims.
        return value if re.fullmatch(r"#[0-9a-f]{3,6}|[a-z]+", value) else ""

    same = bool(color(fg) and color(fg) == color(bg))
    return hidden or same, same, fg, bg

class _VisibleHTML(HTMLParser):
    """Retain visible text and the *associated* anchor label and href only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.stack: list[tuple[str, bool, str, str]] = []
        self.anchor: list[dict] = []
        self.reasons: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        parent_hidden = bool(self.stack and self.stack[-1][1])
        parent_fg, parent_bg = self.stack[-1][2:] if self.stack else ("", "")
        local, same, fg, bg = _style_hidden(
            attrs.get("style") or "", parent_fg, parent_bg)
        invisible = (parent_hidden or tag in _DROP or local or "hidden" in attrs
                     or (attrs.get("aria-hidden") or "").lower() == "true")
        if same:
            self.reasons.add("same_color")
        if invisible and not parent_hidden and tag not in _DROP:
            self.reasons.add("hidden_html")
        if invisible and tag == "a":
            self.reasons.add("hidden_link")
            if attrs.get("href"):
                self.reasons.add("hidden_url")
        if tag == "a":
            href = attrs.get("href") or ""
            self.anchor.append({"href": href, "title": attrs.get("title") or "",
                                "text": [], "hidden": invisible})
        if tag in _BLOCK and not invisible:
            self.parts.append("\n")
        # Raw event handlers and arbitrary attribute values never enter parts.
        self.stack.append((tag, invisible, fg, bg))
        if tag in {"br", "img", "input", "hr", "meta", "link", "source", "wbr"}:
            self.handle_endtag(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        # Tolerate malformed markup without permitting hidden children to escape.
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                del self.stack[idx:]
                break
        if tag == "a" and self.anchor:
            link = self.anchor.pop()
            if not link["hidden"]:
                label = " ".join("".join(link["text"]).split()) or link["title"]
                if label and link["href"]:
                    self.links.append((label, link["href"]))

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1][1]:
            if _URL.search(data):
                self.reasons.add("hidden_url")
            return
        self.parts.append(data)
        if self.anchor:
            self.anchor[-1]["text"].append(data)


def _text(raw: str, reasons: set[str], *, strip_trailing: bool = False) -> str:
    if _ZERO_WIDTH.search(raw):
        reasons.add("zero_width")
    if _BIDI.search(raw):
        reasons.add("bidi_control")
    raw = _ZERO_WIDTH.sub("", _BIDI.sub("", raw))
    # Legacy string readers retain harmless trailing spaces, while normalized
    # reader interfaces opt out *during* analysis, before generation binding.
    trailing_spaces = "" if strip_trailing else raw[len(raw.rstrip(" ")):]
    cleaned: list[str] = []
    for line in raw.splitlines():
        if _INSTRUCTION.search(line):
            reasons.add("model_instruction")
            continue
        for token in _TOKEN.findall(line):
            scripts = ({'latin' if "LATIN" in __import__("unicodedata").name(ch, "")
                        else 'cyrillic' if "CYRILLIC" in __import__("unicodedata").name(ch, "")
                        else 'other' for ch in token})
            if 'latin' in scripts and 'cyrillic' in scripts:
                reasons.add("mixed_script")
        line = " ".join(line.split())
        if line:
            cleaned.append(line)
    normalized = "\n".join(cleaned)
    return (normalized + trailing_spaces if normalized else "")[:100000]


def _version(text: str, status: str, reasons: list[str], source_verified: bool) -> str:
    evidence = json.dumps([REVISION, text, status, reasons, source_verified],
                          ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def _result(text: str, reasons: set[str], source_verified: bool) -> dict:
    reasons.intersection_update(_MESSAGES)
    codes = sorted(reasons)
    status = ("error" if "guard_error" in reasons else
              "flagged" if set(codes) & _POSITIVE else
              "unchecked" if "source_html_unavailable" in reasons else "clean")
    return {
        "text": text,
        "posting_guard": {
            "revision": REVISION, "status": status, "reasons": codes,
            "generation": _version(text, status, codes, source_verified),
            "source_verified": source_verified,
        },
    }


def analyze(text: str | None, *, html_variants=(), source_required: bool = False,
            previous: dict | None = None, strip_trailing: bool = False) -> dict:
    """Inspect all supplied HTML variants before relying on selected plain text.

    Previous positive reasons are monotonic. A provisional missing-HTML marker
    clears only with a complete source witness explicitly passed by the caller.
    """
    try:
        if text is not None and not isinstance(text, str):
            raise TypeError("posting text must be a string")
        reasons: set[str] = set()
        old = previous or {}
        if isinstance(old, dict):
            reasons.update(code for code in old.get("reasons", []) if code in _MESSAGES)
        variants = tuple(html_variants or ())
        verified = bool(variants) and all(isinstance(v, str) and bool(v.strip())
                                         for v in variants)
        visible = ""
        for source in variants:
            if not isinstance(source, str):
                raise TypeError("source HTML must be a string")
            parser = _VisibleHTML()
            parser.feed(source)
            parser.close()
            reasons.update(parser.reasons)
            if len("".join(parser.parts)) > len(visible):
                visible = "".join(parser.parts)
        selected = text or visible
        if re.search(r"<(?:[a-z][\w-]*)\b[^>]*>", selected, re.I):
            parser = _VisibleHTML()
            parser.feed(selected)
            parser.close()
            reasons.update(parser.reasons)
            selected = "".join(parser.parts)
        clean = _text(selected, reasons, strip_trailing=strip_trailing)
        if source_required and not verified:
            reasons.add("source_html_unavailable")
        elif verified:
            reasons.discard("source_html_unavailable")
        return _result(clean, reasons, verified or
                       bool(old.get("source_verified")) and not source_required)
    except Exception:
        return _result("", {"guard_error"} | {
            c for c in (previous or {}).get("reasons", []) if c in _MESSAGES
        }, False)


def merge(text: str, incoming: dict, previous: dict | None,
          *, source_witness: bool = False) -> dict:
    """Merge a new cleaned result into a current row, never dropping findings."""
    old = previous if isinstance(previous, dict) else {}
    source_ok = bool(source_witness and incoming.get("source_verified"))
    result = analyze(text, previous=old, source_required=(
        "source_html_unavailable" in old.get("reasons", []) and not source_ok))
    reasons = set(result["posting_guard"]["reasons"])
    reasons.update(code for code in incoming.get("reasons", []) if code in _MESSAGES)
    if source_ok:
        reasons.discard("source_html_unavailable")
    if old.get("status") == "error" and not source_ok:
        reasons.add("guard_error")
    return _result(result["text"], reasons, source_ok or
                   bool(old.get("source_verified")))


def valid(text: str, guard: dict | None) -> bool:
    """An annotation is a current witness only when its bound text still matches."""
    if not isinstance(guard, dict):
        return False
    codes = guard.get("reasons")
    status = guard.get("status")
    verified = guard.get("source_verified")
    return (guard.get("revision") == REVISION
            and isinstance(text, str)
            and isinstance(codes, list)
            and all(isinstance(c, str) and c in _MESSAGES for c in codes)
            and codes == sorted(set(codes))
            and status in {"clean", "flagged", "unchecked", "error"}
            and type(verified) is bool
            and _result(text, set(codes), verified)["posting_guard"]["status"] == status
            and guard.get("generation") == _version(text, status, codes, verified))


def projection(text: str, guard: dict | None) -> dict:
    """Legacy rows cannot become auto-clean just because a view sanitizes them."""
    if valid(text, guard):
        return {"text": text, "posting_guard": guard}
    inspected = analyze(text)
    meta = inspected["posting_guard"]
    reasons = set(meta["reasons"])
    reasons.add("source_html_unavailable")
    return _result(inspected["text"], reasons, False)


def requires_review(row: dict) -> bool:
    text, guard = row.get("jd_text") or "", row.get("posting_guard")
    return not valid(text, guard) or guard["status"] != "clean"


def authorised(row: dict, *, text: str | None = None) -> bool:
    """Only a currently bound, explicitly reviewed generation admits a risky JD."""
    guard = row.get("posting_guard")
    stored = row.get("jd_text") or ""
    if not valid(stored, guard) or (text is not None and text != stored):
        return False
    return (guard["status"] == "clean"
            or row.get("reviewed_guard_generation") == guard["generation"])


def safe_extractor(html: str, base_url: str, validator) -> dict:
    """Visible links remain title/URL *pairs*; arbitrary HTML never reaches LLM."""
    try:
        parser = _VisibleHTML()
        parser.feed(html)
        parser.close()
        inspected = analyze("".join(parser.parts), html_variants=[html])
        links = []
        for label, href in parser.links:
            url = urljoin(base_url, href)
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and validator(base_url, url):
                links.append({"title": _text(label, set()), "url": url})
        manifest = json.dumps(links, ensure_ascii=False, separators=(",", ":"))
        inspected["extractor_text"] = (
            "VISIBLE CAREER PAGE:\n" + inspected["text"][:35000]
            + "\n\nVISIBLE POSTING LINKS (validated title/url pairs):\n" + manifest)
        return inspected
    except Exception:
        failed = _result("", {"guard_error"}, False)
        failed["extractor_text"] = ""
        return failed


def reason_messages(guard: dict | None) -> list[str]:
    return [_MESSAGES[c] for c in (guard or {}).get("reasons", [])
            if c in _MESSAGES][:10]
