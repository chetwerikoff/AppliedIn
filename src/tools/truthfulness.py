"""Guard active LaTeX résumé facts and bullet counts against a seed.

The tailor may reorder anchors and rewrite bullets, but cannot remove an
active role line or reduce the number of recognized base-used bullets.
"""

from __future__ import annotations

from collections import Counter
import re

# The save boundary and fact validator deliberately share this small inventory.
MACROS = {
    "anchors": (r"\role", r"\resumeSubheading", r"\resumeSubheadingSingle"),
    "bullets": (r"\resumeItem", r"\item"),
}
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_DEFINITIONS = {
    "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
    "NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand",
    "DeclareDocumentCommand", "def", "gdef", "edef", "xdef", "let",
}


def _unescaped(text: str, at: int) -> bool:
    """A TeX control escape consumes one backslash; pairs cancel out."""
    n = 0
    while at > n and text[at - n - 1] == "\\":
        n += 1
    return n % 2 == 0


def _without_comments(latex: str) -> str:
    lines = []
    for line in latex.splitlines(keepends=True):
        for i, char in enumerate(line):
            if char == "%" and _unescaped(line, i):
                line = line[:i] + "".join("\n" if c == "\n" else " " for c in line[i:])
                break
        lines.append(line)
    return "".join(lines)


def _group_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    for i in range(start, len(text)):
        if not _unescaped(text, i):
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)  # A malformed definition cannot expose its body as calls.


def _definition_end(text: str, name: str, at: int) -> int:
    """Skip just TeX macro declarations, including multiline definition bodies."""
    def ws(i: int) -> int:
        while i < len(text) and text[i].isspace():
            i += 1
        return i

    i = ws(at)
    if i < len(text) and text[i] == "*":
        i = ws(i + 1)
    if name == "let":
        end = text.find("\n", i)
        return len(text) if end < 0 else end
    if name in {"def", "gdef", "edef", "xdef"}:
        brace = text.find("{", i)
        return len(text) if brace < 0 else _group_end(text, brace, "{", "}")
    if i < len(text) and text[i] == "{":
        i = _group_end(text, i, "{", "}")
    elif i < len(text) and text[i] == "\\":
        token = _COMMAND.match(text, i)
        i = token.end() if token else i + 1
    else:
        return len(text)
    i = ws(i)
    if name.endswith("DocumentCommand"):
        if i < len(text) and text[i] == "{":
            i = ws(_group_end(text, i, "{", "}"))  # Argument specification.
    else:
        while i < len(text) and text[i] == "[":
            i = ws(_group_end(text, i, "[", "]"))
    return _group_end(text, i, "{", "}") if i < len(text) and text[i] == "{" else len(text)


def _active_content(latex: str) -> str:
    """Leave only résumé content, not comments, preamble or definitions."""
    text = _without_comments(latex)
    for marker in (r"\\begin\s*\{\s*document\s*\}", r"\\end\s*\{\s*document\s*\}"):
        match = next((m for m in re.finditer(marker, text) if _unescaped(text, m.start())), None)
        if match:
            text = text[match.end():] if "begin" in marker else text[:match.start()]

    pieces, cursor = [], 0
    for match in _COMMAND.finditer(text):
        if match.start() < cursor or not _unescaped(text, match.start()):
            continue
        if match.group(1) not in _DEFINITIONS:
            continue
        end = _definition_end(text, match.group(1), match.end())
        pieces.append(text[cursor:match.start()])
        pieces.append("".join("\n" if c == "\n" else " " for c in text[match.start():end]))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def active_calls(latex: str, macros: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return (exact macro, complete active line) for résumé-content calls."""
    allowed = set(macros)
    calls = []
    for line in _active_content(latex).splitlines():
        for match in _COMMAND.finditer(line):
            if not _unescaped(line, match.start()):
                continue
            macro = "\\" + match.group(1)
            if macro not in allowed:
                continue
            # Structural and resumeItem macros take a braced argument; a bare
            # name in ordinary text is not a supported invocation. \item does not.
            if macro != r"\item" and not re.match(r"\s*\{", line[match.end():]):
                continue
            calls.append((macro, line.strip()))
    return calls


def used_macros(base_latex: str) -> dict[str, tuple[str, ...]]:
    """Only supported macro types with actual calls in the base résumé content."""
    used = {macro for macro, _ in active_calls(base_latex, (*MACROS["anchors"], *MACROS["bullets"]))}
    return {kind: tuple(m for m in inventory if m in used)
            for kind, inventory in MACROS.items()}


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().lower()


def validate(base_latex: str, tailored_latex: str) -> list[str]:
    """Return lost/altered active structural lines, preserving multiplicity."""
    anchors = used_macros(base_latex)["anchors"]
    remaining = Counter(_norm(line) for _, line in active_calls(tailored_latex, anchors))
    missing = []
    for _, line in active_calls(base_latex, anchors):
        normalized = _norm(line)
        if remaining[normalized]:
            remaining[normalized] -= 1
        else:
            missing.append(line)
    return missing
