"""Tests for tools/bazel/owned_files_check.py.

Every tracked file has to land in exactly one bucket: target-owned,
control-plane, ledger-excluded, or unowned. The label-to-path mapping is the
part that silently reports almost everything unowned, so it is pinned first.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Optional, Sequence

import pytest

import owned_files_check

REPO_FILES = {
    ".bazelrc": "build --enable_bzlmod\n",
    "MODULE.bazel": "module(name = \"vlo\")\n",
    "pkg/BUILD.bazel": 'filegroup(\n    name = "srcs",\n    srcs = ["a.py"],\n)\n',
    "pkg/a.py": "print()\n",
    "docs/note.md": "# note\n",
    "vendor/lib.js": "export const x = 1;\n",
    "extra/loose.txt": "nobody owns me\n",
}

BAZELIGNORE = "# third-party trees are not part of the workspace\nvendor/\n\n   \n"

QUERY_OUTPUT = "//pkg:a.py\n\n   \nnot a label\n@@//pkg:c.txt\n"


def invoke(root: Path, *extra: str) -> int:
    """Run ``owned_files_check.main()`` with ``--root`` pinned to ``root``."""
    sys.argv = ["owned_files_check.py", "--root", str(root), *extra]
    return owned_files_check.main()


def write_ledger(root: Path, lines: Sequence[str]) -> Path:
    ledger = root / "bazel-unowned.txt"
    ledger.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return ledger


# --- label mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("//pkg:file", "pkg/file"),
        ("//:file", "file"),
        ("//a/b/c:file.txt", "a/b/c/file.txt"),
        ("@@//pkg:file", "pkg/file"),
        ("//:BUILD.bazel", "BUILD.bazel"),
        # A repo-qualified label names a file inside that repository, so it owns
        # nothing in this workspace; folding it onto the workspace path was the
        # false negative this gate exists to prevent.
        ("@@rules_python//pkg:file", None),
        ("@local//pkg:file", None),
        ("@local//:file", None),
        # "@//pkg:file" is Bazel's canonical main-repo spelling, but the script
        # reads any single-@ prefix as a repository name, so it maps to nothing.
        ("@//pkg:file", None),
        ("pkg:file", None),
        ("//pkg/no-colon", None),
        ("//pkg/a.py", None),
        ("@rules_python:file", None),
        ("", None),
    ],
)
def test_label_to_path_maps_only_main_repo_labels(
    label: str, expected: Optional[str]
) -> None:
    assert owned_files_check.label_to_path(label) == expected


def test_owned_paths_skips_lines_that_are_not_labels(patch_run) -> None:
    recorder = patch_run(
        owned_files_check,
        bazelisk_stdout="//pkg:a.py\n@@//x:y.txt\n\n   \nnot a label\n@ext//z:w.py\n",
    )
    assert owned_files_check.owned_paths(Path("/repo")) == {"pkg/a.py", "x/y.txt"}
    assert recorder.commands() == [
        "bazelisk query kind('source file', deps(//...)) --output=label"
    ]


def test_owned_paths_drops_external_repo_labels(patch_run) -> None:
    patch_run(
        owned_files_check,
        bazelisk_stdout="@ext//z:w.py\n@@rules_python//z:w.py\n@//z:w.py\n//pkg:a.py\n",
    )
    assert owned_files_check.owned_paths(Path("/repo")) == {"pkg/a.py"}


# --- control plane and .bazelignore -----------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".bazelrc",
        "MODULE.bazel",
        "MODULE.bazel.lock",
        "deep/nested/.bazelversion",
        "pkg/BUILD",
        "pkg/BUILD.bazel",
        "sub/WORKSPACE",
        "sub/MODULE.bazel",
        # Names that only start with a control-plane entry are caught by the
        # prefix rules rather than by membership in CONTROL_PLANE.
        "generated/WORKSPACE.bazel.inc",
        "generated/MODULE.bazel.inc",
        "ide/vflo.bazelproject",
    ],
)
def test_is_control_plane_accepts_build_inputs(path: str) -> None:
    assert owned_files_check.is_control_plane(path) is True


@pytest.mark.parametrize("path", ["pkg/a.py", "README.md", "pkg/BUILD.bazel.md"])
def test_is_control_plane_rejects_ordinary_files(path: str) -> None:
    assert owned_files_check.is_control_plane(path) is False


def test_bazel_ignored_dirs_strips_comments_and_slashes(git_repo) -> None:
    repo = git_repo({".bazelignore": BAZELIGNORE})
    assert owned_files_check.bazel_ignored_dirs(repo) == {"vendor"}


def test_bazel_ignored_dirs_is_empty_without_the_file(git_repo) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    assert owned_files_check.bazel_ignored_dirs(repo) == set()


# --- ledger -----------------------------------------------------------------


def test_read_ledger_parses_tab_and_space_forms(git_repo) -> None:
    repo = git_repo(
        {
            "bazel-unowned.txt": (
                "# why these are outside the graph\n"
                "\n"
                "   \n"
                "   # indented comment\n"
                "docs/*.md\towned by the docs target\n"
                "assets/logo.png   shipped by the web bundle\n"
                "stray.txt\n"
            )
        }
    )
    entries = owned_files_check.read_ledger(repo / "bazel-unowned.txt")
    assert [(pattern, reason) for _, pattern, reason in entries] == [
        ("docs/*.md", "owned by the docs target"),
        ("assets/logo.png", "shipped by the web bundle"),
        ("stray.txt", ""),
    ]


def test_read_ledger_tolerates_a_missing_file(tmp_path) -> None:
    assert owned_files_check.read_ledger(tmp_path / "nope.txt") == []


def test_read_ledger_globs_are_anchored_whole_path(git_repo) -> None:
    repo = git_repo({"bazel-unowned.txt": "docs/*.md\treason here\n"})
    (regex, _, _) = owned_files_check.read_ledger(repo / "bazel-unowned.txt")[0]
    assert regex.match("docs/note.md")
    assert regex.match("docs/a/b/note.md")
    assert not regex.match("pkd/docs/note.md")
    assert not regex.match("docs/note.md.bak")


def test_read_ledger_question_mark_matches_one_character(git_repo) -> None:
    repo = git_repo({"bazel-unowned.txt": "docs/note?.md\treason here\n"})
    (regex, _, _) = owned_files_check.read_ledger(repo / "bazel-unowned.txt")[0]
    assert regex.match("docs/note1.md")
    assert not regex.match("docs/note.md")


# --- the run helper ---------------------------------------------------------


def test_run_returns_stdout_on_success(git_repo) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    assert owned_files_check.run(["git", "ls-files"], str(repo)).strip() == "pkg/a.py"


def test_run_exits_2_and_echoes_stderr_on_failure(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        owned_files_check.run(["git", "definitely-not-a-subcommand"], str(tmp_path))
    assert raised.value.code == 2
    assert capsys.readouterr().err.strip()


# --- main -------------------------------------------------------------------


def _full_repo(git_repo) -> Path:
    repo = git_repo({**REPO_FILES, ".bazelignore": BAZELIGNORE})
    write_ledger(repo, ["docs/*.md\towned by the docs target"])
    return repo


def test_an_external_label_cannot_own_a_workspace_file(
    git_repo, patch_run, capsys
) -> None:
    """The collision that mattered: an external path must not mark a file owned."""
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_run(
        owned_files_check,
        bazelisk_stdout="@ext//pkg:a.py\n@@rules_python//pkg:a.py\n",
    )
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert (
        "UNOWNED pkg/a.py  (no target owns it and no ledger entry covers it)" in out
    )
    assert "1 tracked | 0 target-owned" in out
    assert invoke(repo, "--json") == 1
    assert json.loads(capsys.readouterr().out)["owned_labels"] == 0


def test_the_main_repo_spelling_of_the_same_label_does_own_the_file(
    git_repo, patch_run, capsys
) -> None:
    """Control for the test above: only a main-repo label owns a workspace file."""
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_run(owned_files_check, bazelisk_stdout="//pkg:a.py\n")
    assert invoke(repo) == 0
    assert "1 tracked | 1 target-owned" in capsys.readouterr().out


def test_clean_run_buckets_every_tracked_file(git_repo, patch_run, capsys) -> None:
    files = dict(REPO_FILES)
    del files["extra/loose.txt"]
    repo = git_repo({**files, ".bazelignore": BAZELIGNORE})
    write_ledger(repo, ["docs/*.md\towned by the docs target"])
    patch_run(owned_files_check, bazelisk_stdout=QUERY_OUTPUT)
    assert invoke(repo) == 0
    summary = capsys.readouterr().out
    assert "summary: 7 tracked | 1 target-owned | 5 control-plane |" in summary
    assert "1 ledger-excluded (reasoned) | 0 unowned | 0 stale ledger entries" in summary


def test_unowned_file_is_reported(git_repo, patch_run, capsys) -> None:
    repo = _full_repo(git_repo)
    patch_run(owned_files_check, bazelisk_stdout=QUERY_OUTPUT)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert (
        "UNOWNED extra/loose.txt  (no target owns it and no ledger entry covers it)"
        in out
    )


def test_ledger_reason_hides_a_file_from_the_report(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    write_ledger(repo, ["docs/*.md\towned by the docs target"])
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo) == 0
    assert "1 ledger-excluded (reasoned)" in capsys.readouterr().out


def test_ledger_entry_without_a_reason_is_unowned(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    write_ledger(repo, ["docs/*.md"])
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo) == 1
    assert "UNOWNED docs/note.md  (ledger entry carries no reason)" in (
        capsys.readouterr().out
    )


def test_first_matching_ledger_entry_wins(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    write_ledger(repo, ["docs/*.md\tfirst match", "docs/note.md\tsecond match"])
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo) == 0
    assert "1 ledger-excluded (reasoned)" in capsys.readouterr().out


def test_ignored_directory_lands_in_the_control_bucket(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"vendor/deep/lib.js": "x\n", "top.txt": "y\n"})
    (repo / ".bazelignore").write_text("vendor/\ntop.txt\n", encoding="utf-8")
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo) == 0
    assert "2 tracked | 0 target-owned | 2 control-plane" in capsys.readouterr().out


def test_stale_ledger_entries_are_only_reported_without_strict_mode(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    write_ledger(repo, ["docs/*.md\treason", "gone/*.txt\tno longer here"])
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo) == 0
    assert "1 stale ledger entries" in capsys.readouterr().out


def test_strict_ledger_turns_stale_entries_into_failures(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    write_ledger(repo, ["docs/*.md\treason", "gone/*.txt\tno longer here"])
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo, "--strict-ledger") == 1
    out = capsys.readouterr().out
    assert "UNOWNED gone/*.txt  (ledger entry matches nothing (stale))" in out


def test_json_report_accounts_for_every_file(git_repo, patch_run, capsys) -> None:
    repo = _full_repo(git_repo)
    write_ledger(repo, ["docs/*.md\towned by the docs target", "gone/*.txt\tstale"])
    patch_run(owned_files_check, bazelisk_stdout=QUERY_OUTPUT)
    assert invoke(repo, "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["tracked"] == 8
    assert payload["control_plane"] == 5
    assert payload["excluded"] == 1
    assert payload["unowned"] == [
        {"path": "extra/loose.txt", "why": "no target owns it and no ledger entry covers it"}
    ]
    assert payload["stale_ledger"] == ["gone/*.txt"]
    assert payload["target_owned"] == payload["tracked"] - 5 - 1 - 1


def test_json_report_is_clean_when_everything_is_owned(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_run(owned_files_check, bazelisk_stdout="//pkg:a.py\n")
    assert invoke(repo, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "tracked": 1,
        "target_owned": 1,
        "owned_labels": 1,
        "control_plane": 0,
        "excluded": 0,
        "stale_ledger": [],
        "unowned": [],
    }


def test_ledger_path_may_be_absolute(git_repo, patch_run, tmp_path, capsys) -> None:
    repo = git_repo({"docs/note.md": "# note\n"})
    outside = tmp_path / "outside-ledger.txt"
    outside.write_text("docs/*.md\tkept outside the repo\n", encoding="utf-8")
    patch_run(owned_files_check, bazelisk_stdout="")
    assert invoke(repo, "--ledger", str(outside)) == 0
    assert "1 ledger-excluded (reasoned)" in capsys.readouterr().out


def test_missing_ledger_is_not_an_environment_error(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_run(owned_files_check, bazelisk_stdout="//pkg:a.py\n")
    assert invoke(repo) == 0
    assert "0 ledger-excluded" in capsys.readouterr().out


def test_empty_manifest_is_an_environment_error(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_run(owned_files_check, bazelisk_stdout="", git_stdout={"ls-files": ""})
    assert invoke(repo) == 2
    assert "error: git ls-files returned nothing" in capsys.readouterr().err


# --- CLI entry point --------------------------------------------------------


def test_module_main_exits_with_the_status_code(
    git_repo, patch_subprocess_run, tmp_path
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_subprocess_run(bazelisk_stdout="//pkg:a.py\n")
    script = Path(owned_files_check.__file__).resolve()
    sys.argv = [str(script), "--root", str(repo)]
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(script), run_name="__main__")
    assert raised.value.code == 0
