"""Reject C0/DEL bytes before either LaTeX engine without changing other source.

Synthetic tailoring can inject U+0019 into a skills line and make XeTeX halt.
Both compiler paths must stage cleaned input without exercising any real engine.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from tools import render

SOURCE = (
    "\\documentclass{article}\r\n"
    "\\usepackage{times}\r\n"
    "\\pdfinfo{/Title (Test User)}\n"
    "\\begin{document}\r\n"
    "Test User\tworked at example-co: design\x00, testing\x19, delivery\x7f.\n"
    "\\end{document}\r\n"
)
CLEANED = SOURCE.replace("\x00", "").replace("\x19", "").replace("\x7f", "")


def _warning_messages(caplog):
    return [record.getMessage() for record in caplog.records if record.name == render.__name__]


def test_exact_controls_removed_with_count_only_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=render.__name__):
        result = render._strip_c0_controls(SOURCE)

    assert result == CLEANED
    assert "\t" in result and "\r\n" in result and "\n" in result
    messages = _warning_messages(caplog)
    assert len(messages) == 1
    assert re.search(r"\b3\b", messages[0])
    for private_part in (SOURCE, "Test User", "example-co", "design", "\x00", "\x19", "\x7f"):
        assert private_part not in messages[0]


def test_all_disallowed_c0_and_del_are_removed(caplog):
    source = "É" + "".join(map(chr, range(32))) + "\x7f" + "漢"
    with caplog.at_level(logging.WARNING, logger=render.__name__):
        result = render._strip_c0_controls(source)
    assert result == "É\t\n\r漢"
    assert len(_warning_messages(caplog)) == 1
    assert re.search(r"\b30\b", _warning_messages(caplog)[0])


def test_clean_utf8_and_original_line_endings_preserved_byte_for_byte(caplog):
    clean = "\\documentclass{article}\r\n\\begin{document}\nTest User\t— café 漢字\rEnd\r\n"
    with caplog.at_level(logging.WARNING, logger=render.__name__):
        output = render._strip_c0_controls(clean)
    assert output.encode("utf-8") == clean.encode("utf-8")
    assert not _warning_messages(caplog)


@pytest.mark.parametrize("engine", ["pdflatex", "tectonic"])
def test_every_compiler_stages_filtered_source_without_running_tex(engine, monkeypatch, caplog):
    staged = {}

    def fake_which(name):
        return f"/fake/{name}" if name == engine else None

    def fake_run(command, tmp, name):
        staged["command"] = command
        staged["source"] = (Path(tmp) / f"{name}.tex").read_bytes().decode("utf-8")
        return b"synthetic-pdf-bytes"

    monkeypatch.setattr(render.shutil, "which", fake_which)
    monkeypatch.setattr(render, "_run", fake_run)
    with caplog.at_level(logging.WARNING, logger=render.__name__):
        result = render.render_pdf(SOURCE)

    assert result == b"synthetic-pdf-bytes"
    assert staged["command"][0] == f"/fake/{engine}"
    expected = CLEANED if engine == "pdflatex" else render._sanitize(CLEANED)
    assert staged["source"] == expected
    assert not any(char in staged["source"] for char in ("\x00", "\x19", "\x7f"))
    assert "\t" in staged["source"]
    if engine == "pdflatex":
        assert r"\usepackage{times}" in staged["source"]
        assert r"\pdfinfo" in staged["source"]
        assert "\r\n" in staged["source"]
    else:
        assert r"\usepackage{newtxtext}" in staged["source"]
        assert r"\pdfinfo" not in staged["source"]
    messages = _warning_messages(caplog)
    assert len(messages) == 1
    assert re.search(r"\b3\b", messages[0])
    assert "Test User" not in messages[0]
    assert "example-co" not in messages[0]
