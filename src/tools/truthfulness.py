"""Guard active LaTeX résumé facts and bullet counts against a seed.

The tailor may reorder anchors and rewrite bullets, but cannot remove an
active role line or reduce the number of recognized base-used bullets.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Mapping, Sequence

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


# Do not infer a title slot from an unfamiliar arity or a project/short-form
# heading. This is intentionally not a general TeX parser.
_TITLE_LAYOUTS = {
    r"\role": (3, 0, 1),
    r"\resumeSubheading": (4, 0, 2),
}


@dataclass(frozen=True)
class _TitleSlot:
    line: str
    employer: str
    title: str
    start: int
    end: int
    signature: str


@dataclass(frozen=True)
class TitleOption:
    """One independently identifiable seed role and its permitted replacements."""
    seed_line: str
    seed_title: str
    approved: tuple[str, ...]
    start: int
    end: int


def _title_slot(line: str) -> _TitleSlot | None:
    """Parse only a balanced, single-line, exact supported role call.

    The title argument must have no nested TeX groups; other arguments may
    retain their seed markup but are never available for substitution.
    """
    call = _COMMAND.match(line)
    if not call:
        return None
    macro = "\\" + call.group(1)
    layout = _TITLE_LAYOUTS.get(macro)
    if layout is None:
        return None
    arity, employer_arg, title_arg = layout
    # A second structural call anywhere on the line makes slot ownership
    # ambiguous, even if a generic TeX parser could pick one interpretation.
    for nested in _COMMAND.finditer(line, call.end()):
        if "\\" + nested.group(1) in MACROS["anchors"]:
            return None

    pos, args = call.end(), []
    for _ in range(arity):
        while pos < len(line) and line[pos].isspace():
            pos += 1
        if pos >= len(line) or line[pos] != "{":
            return None
        start, depth, pos = pos + 1, 1, pos + 1
        while pos < len(line) and depth:
            if line[pos] == "{" and _unescaped(line, pos):
                depth += 1
            elif line[pos] == "}" and _unescaped(line, pos):
                depth -= 1
            pos += 1
        if depth:
            return None
        args.append((start, pos - 1))
    if line[pos:].strip():
        return None
    start, end = args[title_arg]
    title = line[start:end]
    if not title.strip() or "{" in title or "}" in title or "%" in title:
        return None
    employer_start, employer_end = args[employer_arg]
    employer = line[employer_start:employer_end].strip()
    return _TitleSlot(
        line=line, employer=employer, title=title, start=start, end=end,
        signature=_norm(line[:start] + "<title-slot>" + line[end:]),
    )


def eligible_title_options(
    base_latex: str, approved_titles: Mapping[str, Sequence[str]],
) -> tuple[TitleOption, ...]:
    """Return only approved values attached to exact, unique *seed* anchors.

    Two jobs at one employer may each independently use company-scoped titles
    when their non-title seed signatures distinguish them. Same-employer
    duplicates whose signatures differ only in title fail closed.
    """
    anchors = used_macros(base_latex)["anchors"]
    slots = [_title_slot(line) for _, line in active_calls(base_latex, anchors)]
    signatures = Counter(s.signature for s in slots if s is not None)
    options = []
    for slot in slots:
        if slot is None or signatures[slot.signature] != 1:
            continue
        approved = tuple(approved_titles.get(slot.employer, ()))
        if approved:
            options.append(TitleOption(
                slot.line, slot.title, approved, slot.start, slot.end,
            ))
    return tuple(options)


def validate(
    base_latex: str, tailored_latex: str,
    approved_titles: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Return lost/altered active structural lines, preserving multiplicity.

    The normal Counter guard is unchanged without configured exceptions.
    With exceptions, each missing seed line must consume its OWN unused active
    target witness equal to that exact seed line except for one approved,
    properly encoded title argument. No anchor may borrow a different role's
    parsed witness, and admitting a title edit cannot add another anchor.
    """
    anchors = used_macros(base_latex)["anchors"]
    new_calls = active_calls(tailored_latex, anchors)
    remaining = Counter(_norm(line) for _, line in new_calls)
    missing = []
    for _, line in active_calls(base_latex, anchors):
        normalized = _norm(line)
        if remaining[normalized]:
            remaining[normalized] -= 1
        else:
            missing.append(line)
    if not missing or not approved_titles:
        return missing

    options = {
        _norm(option.seed_line): option
        for option in eligible_title_options(base_latex, approved_titles)
    }
    unresolved = []
    admitted = 0
    for line in missing:
        option = options.get(_norm(line))
        matched = False
        if option:
            for title in option.approved:
                expected = _norm(line[:option.start] + title + line[option.end:])
                if remaining[expected]:
                    remaining[expected] -= 1
                    admitted += 1
                    matched = True
                    break
        if not matched:
            unresolved.append(line)

    if admitted and any(remaining.values()):
        # The legacy guard permits extra anchors in an unchanged résumé; do
        # not expand that legacy allowance into a variant's active-call count.
        for _, line in new_calls:
            normalized = _norm(line)
            if remaining[normalized]:
                unresolved.append("Unexpected active role/heading: " + line)
                remaining[normalized] -= 1
    return unresolved
