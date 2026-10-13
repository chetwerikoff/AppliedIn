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


_OPAQUE_ENV = re.compile(r"\s*\{\s*(comment|verbatim\*?|Verbatim\*?|lstlisting|minted)\s*\}")


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def _without_disabled_regions(text: str) -> str:
    """Exclude known non-executing TeX regions from structural witnesses.

    Do not guess which branch of an arbitrary TeX conditional executes. Such
    content is unverifiable at this narrow boundary, so return no active calls
    and let the save guards refuse it.
    """
    pieces, cursor, scan = [], 0, 0
    while token := _COMMAND.search(text, scan):
        if not _unescaped(text, token.start()):
            scan = token.end()
            continue
        name = token.group(1)
        end = None
        if name == "iffalse":
            depth = 1
            for nested in _COMMAND.finditer(text, token.end()):
                if not _unescaped(text, nested.start()):
                    continue
                command = nested.group(1)
                if command.startswith("if"):
                    depth += 1
                elif command == "fi":
                    depth -= 1
                    if depth == 0:
                        end = nested.end()
                        break
                elif command in {"else", "or"} and depth == 1:
                    # An else branch may execute; never treat its roles as absent.
                    return ""
            if end is None:
                end = len(text)  # Unterminated false branch stays excluded.
        elif name == "begin":
            environment = _OPAQUE_ENV.match(text, token.end())
            if environment:
                closing = re.compile(
                    r"\\end\s*\{\s*" + re.escape(environment.group(1)) + r"\s*\}"
                )
                marker = next(
                    (m for m in closing.finditer(text, environment.end())
                     if _unescaped(text, m.start())), None
                )
                end = marker.end() if marker else len(text)
        elif name.startswith("if") or name in {"else", "fi", "or", "verb"}:
            # Non-literal conditionals and inline verbatim require TeX execution
            # to determine visibility. Refuse instead of accepting false facts.
            return ""
        if end is None:
            scan = token.end()
            continue
        pieces.append(text[cursor:token.start()])
        pieces.append(_blank(text[token.start():end]))
        cursor = scan = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _active_content(latex: str) -> str:
    """Leave only verifiable active résumé content, not inert TeX witnesses."""
    text = _without_comments(latex)
    pieces, cursor = [], 0
    for match in _COMMAND.finditer(text):
        if match.start() < cursor or not _unescaped(text, match.start()):
            continue
        if match.group(1) not in _DEFINITIONS:
            continue
        end = _definition_end(text, match.group(1), match.end())
        pieces.append(text[cursor:match.start()])
        pieces.append(_blank(text[match.start():end]))
        cursor = end
    pieces.append(text[cursor:])
    text = "".join(pieces)

    begin = next(
        (m for m in re.finditer(r"\\begin\s*\{\s*document\s*\}", text)
         if _unescaped(text, m.start())), None
    )
    if begin:
        text = text[begin.end():]
    text = _without_disabled_regions(text)
    # Do not let a fake \end{document} inside an inactive region truncate the
    # actual content. It must be searched *after* hiding inactive regions.
    end = next(
        (m for m in re.finditer(r"\\end\s*\{\s*document\s*\}", text)
         if _unescaped(text, m.start())), None
    )
    return text[:end.start()] if end else text


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
