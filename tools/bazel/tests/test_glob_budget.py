"""Tests for tools/bazel/glob_budget.py.

The gate needs three things from every ``glob(``: a policy comment within the
context window, a per-file tally, and a total that stays under ``--max``. Each
test builds a scratch git repo so ``git ls-files`` is real and cheap.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

import glob_budget

ANNOTATED = (
    "# glob-policy: the tree is the contract\n"
    "filegroup(\n"
    '    name = "payload",\n'
    "    srcs = glob(\n"
    '        ["**/*"],\n'
    "    ),\n"
    ")\n"
)

BARE = (
    "filegroup(\n"
    '    name = "srcs",\n'
    "    srcs = glob(\n"
    '        ["*.py"],\n'
    "    ),\n"
    ")\n"
)

NO_GLOB = 'filegroup(\n    name = "srcs",\n    srcs = ["a.py"],\n)\n'


def invoke(root: Path, *extra: str) -> int:
    """Run ``glob_budget.main()`` with ``--root`` pinned to ``root``."""
    sys.argv = ["glob_budget.py", "--root", str(root), *extra]
    return glob_budget.main()


# --- manifest filtering -----------------------------------------------------


def test_tracked_build_files_keeps_only_build_inputs(git_repo) -> None:
    repo = git_repo(
        {
            "BUILD.bazel": NO_GLOB,
            "pkg/BUILD": NO_GLOB,
            "rules/defs.bzl": "def x(): pass\n",
            ".bazelrc": "build --enable_bzlmod\n",
            "pkg/app.py": "print()\n",
            "docs/BUILD.bazel.md": "not a build file\n",
        }
    )
    assert sorted(glob_budget.tracked_build_files(repo)) == [
        ".bazelrc",
        "BUILD.bazel",
        "pkg/BUILD",
        "rules/defs.bzl",
    ]


def test_glob_re_ignores_prefixed_lookalikes() -> None:
    assert glob_budget.GLOB_RE.search("srcs = glob(")
    assert glob_budget.GLOB_RE.search("srcs=glob  (")
    assert not glob_budget.GLOB_RE.search("teleglob(")
    assert not glob_budget.GLOB_RE.search("module.glob(")


# --- annotation window ------------------------------------------------------


def test_annotated_glob_is_clean(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    assert invoke(repo) == 0
    out = capsys.readouterr().out
    assert "UNANNOTATED" not in out
    assert "  1  pkg/BUILD.bazel" in out
    assert "summary: 1 glob() in 1 BUILD files, budget any, 0 unannotated" in out


def test_bare_glob_is_reported_with_line_number(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": BARE})
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "UNANNOTATED pkg/BUILD.bazel:3" in out
    assert "srcs = glob(" in out
    assert "1 unannotated" in out


def test_policy_comment_outside_the_context_window_is_a_violation(
    git_repo, capsys
) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    assert invoke(repo, "--context", "1") == 1
    out = capsys.readouterr().out
    assert "UNANNOTATED pkg/BUILD.bazel:4" in out


def test_policy_comment_on_the_glob_line_counts(git_repo) -> None:
    repo = git_repo(
        {"BUILD.bazel": 'filegroup(\n    srcs = glob(["*"],  # glob-policy: payload\n)\n'}
    )
    assert invoke(repo) == 0


def test_policy_keyword_needs_a_reason_to_count(git_repo, capsys) -> None:
    repo = git_repo({"BUILD.bazel": 'srcs = glob(["*"])  # glob-policy:\n'})
    assert invoke(repo) == 1
    assert "UNANNOTATED BUILD.bazel:1" in capsys.readouterr().out


def test_policy_comment_is_case_insensitive(git_repo) -> None:
    repo = git_repo({"BUILD.bazel": "# GLOB-POLICY: shipped tree\nsrcs = glob(['*'])\n"})
    assert invoke(repo) == 0


# --- accounting -------------------------------------------------------------


def test_files_without_globs_are_not_tallied(git_repo, capsys) -> None:
    repo = git_repo({"quiet/BUILD.bazel": NO_GLOB, "loud/BUILD.bazel": ANNOTATED})
    assert invoke(repo) == 0
    out = capsys.readouterr().out
    assert "loud/BUILD.bazel" in out
    assert "quiet/BUILD.bazel" not in out
    assert "summary: 1 glob() in 1 BUILD files" in out


def test_per_file_counts_are_sorted_by_count_then_name(git_repo, capsys) -> None:
    repo = git_repo(
        {
            "a/BUILD.bazel": ANNOTATED,
            "b/BUILD.bazel": ANNOTATED + ANNOTATED,
            "c/BUILD.bazel": ANNOTATED + ANNOTATED,
        }
    )
    assert invoke(repo) == 0
    out = capsys.readouterr().out.splitlines()
    ranked = [line for line in out if line.startswith("    2  ")]
    assert [line.strip() for line in ranked] == ["2  b/BUILD.bazel", "2  c/BUILD.bazel"]
    assert "summary: 5 glob() in 3 BUILD files" in "\n".join(out)


def test_budget_exceeded_is_a_violation(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED + ANNOTATED})
    assert invoke(repo, "--max", "1") == 1
    out = capsys.readouterr().out
    assert "error: 2 glob() exceeds the budget of 1" in out
    assert "budget 1" in out


def test_budget_not_exceeded_is_clean(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    assert invoke(repo, "--max", "1") == 0
    assert "budget 1" in capsys.readouterr().out


def test_report_only_mode_prints_any_budget(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": BARE})
    assert invoke(repo) == 1
    assert "budget any" in capsys.readouterr().out


def test_json_output_reports_the_full_state(git_repo, capsys) -> None:
    repo = git_repo(
        {"annotated/BUILD.bazel": ANNOTATED, "bare/BUILD.bazel": BARE}
    )
    assert invoke(repo, "--json", "--max", "0") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 2
    assert payload["max"] == 0
    assert payload["budget_hit"] is True
    assert payload["per_file"] == {"annotated/BUILD.bazel": 1, "bare/BUILD.bazel": 1}
    assert payload["unannotated"] == [
        {"file": "bare/BUILD.bazel", "line": 3, "text": "srcs = glob("}
    ]


def test_json_output_is_machine_readable_when_clean(git_repo, capsys) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    assert invoke(repo, "--json", "--max", "5") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["budget_hit"] is False
    assert payload["unannotated"] == []


# --- environment failures ---------------------------------------------------


def test_unreadable_build_file_is_an_environment_error(
    git_repo, capsys
) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    (repo / "pkg" / "BUILD.bazel").unlink()
    assert invoke(repo) == 2
    err = capsys.readouterr().err
    assert "error: cannot read pkg/BUILD.bazel:" in err


def test_long_offending_text_is_truncated(git_repo, capsys) -> None:
    filler = "x" * 120
    repo = git_repo({"BUILD.bazel": "srcs = glob([%r])\n" % filler})
    assert invoke(repo) == 1
    offender = capsys.readouterr().out.splitlines()[0]
    assert len(offender.split("  ", 1)[1]) <= 90


# --- CLI entry point --------------------------------------------------------


def test_module_main_exits_with_the_status_code(git_repo, tmp_path) -> None:
    repo = git_repo({"pkg/BUILD.bazel": ANNOTATED})
    script = Path(glob_budget.__file__).resolve()
    sys.argv = [str(script), "--root", str(repo)]
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(script), run_name="__main__")
    assert raised.value.code == 0
