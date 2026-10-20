"""Private, exact employer-keyed literal titles use only synthetic temp data."""

from pathlib import Path
import subprocess

import pytest

from core.role_titles import BASENAME, literal_title, load_approved_titles


def test_missing_config_and_allowed_literal_encoding(tmp_path):
    assert load_approved_titles(tmp_path) == {}
    assert literal_title("R&D Role (Level 2) / QA: Lead") == r"R\&D Role (Level 2) / QA: Lead"
    assert literal_title("Role B") == "Role B"
    for unsafe in ("Role B%hidden", r"\textbf{Role B}", "Role {B}",
                   "Role_B", "Role #1", "Role $B", "Role ~B", "Role ^B",
                   "Role\nB", " Role B", "Role B ", ""):
        assert literal_title(unsafe) is None


def test_company_list_and_invalid_yaml_fail_closed(tmp_path):
    path = tmp_path / BASENAME
    path.write_text("example-co: [Role B, R&D Role, Role B]\nunrelated-co: [Role X]\n",
                    encoding="utf-8")
    assert load_approved_titles(tmp_path) == {
        "example-co": ("Role B", r"R\&D Role"),
        "unrelated-co": ("Role X",),
    }
    for malformed in (
        "example-co: [Role B", "example-co: Role B",
        "example-co: [Role B%hidden]", "example-co: [Role B, {nested: yes}]",
        "example-co: [Role B]\nunrelated-co: [Role $X]",
        "example-co: [Role B]\nexample-co-lab: invalid",
    ):
        path.write_text(malformed, encoding="utf-8")
        assert load_approved_titles(tmp_path) == {}
    path.write_text("example-co: [Role B]\n", encoding="utf-8")
    assert load_approved_titles(tmp_path)["example-co"] == ("Role B",)


def test_invalid_or_oversized_input_does_not_expose_partial_options(tmp_path):
    path = tmp_path / BASENAME
    path.write_text("example-co: [Role B]\nother: [Role #1]\n", encoding="utf-8")
    assert load_approved_titles(tmp_path) == {}
    path.write_text("example-co: [" + ", ".join(["Role B"] * 7000) + "]",
                    encoding="utf-8")
    assert load_approved_titles(tmp_path) == {}


def test_basename_gitignore_covers_custom_nested_repo_local_dirs():
    root = Path(__file__).resolve().parents[2]
    rule = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "role-titles.yaml" in rule
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", "some-custom-dir/sub/role-titles.yaml"],
        cwd=root, capture_output=True, check=False,
    )
    assert result.returncode == 0
