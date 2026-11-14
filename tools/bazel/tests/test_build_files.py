"""Tests for tools/bazel/build_files.py.

The generator owns every generated BUILD.bazel, so the tests pin three things:
the bucket a file lands in, when a directory is allowed to fall back to a glob,
and what ``--check`` calls drift. Every git query is answered from a canned
manifest so no test walks the real repository.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

import build_files

HEADER_TEXT = build_files.HEADER


def invoke(root: Path, *extra: str) -> int:
    """Run ``build_files.main()`` with ``--root`` pinned to ``root``."""
    sys.argv = ["build_files.py", "--root", str(root), *extra]
    return build_files.main()


def install_run(patch_run, manifest: Sequence[str], untracked: Sequence[str] = ()):
    """Answer the two ``git ls-files`` queries the generator makes."""
    return patch_run(
        build_files,
        git_stdout={
            "--others": "\0".join(untracked),
            "ls-files": "\0".join(manifest),
        },
    )


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("backend/tests/test_agent.py", "tests"),
        ("src/test_agent.py", "tests"),
        ("src/agent_test.py", "tests"),
        ("src/agent.test.js", "tests"),
        ("src/agent.test.jsx", "tests"),
        ("src/agent.test.ts", "tests"),
        ("src/agent.test.tsx", "tests"),
        ("src/agent.test.mjs", "tests"),
        ("src/agent.test.cjs", "tests"),
        ("src/agent.test.mts", "tests"),
        ("src/agent.spec.js", "tests"),
        ("src/agent.spec.tsx", "tests"),
        # The pattern needs a real ".test." / ".spec." boundary before a known
        # script extension, so these only look like tests.
        ("src/agent.mtest.cjs", "sources"),
        ("src/testimonials.tsx", "sources"),
        ("src/agent.test.css", "sources"),
        ("a/fixtures/clip.json", "fixtures"),
        ("a/fixture/clip.json", "fixtures"),
        ("a/__snapshots__/frame.snap", "fixtures"),
        ("frontend/public/robots.txt", "assets"),
        ("backend/assets/lut.cube", "assets"),
        ("docs/README.md", "docs"),
        ("notes.MARKDOWN", "docs"),
        ("scripts/bootstrap.sh", "scripts"),
        ("scripts/build.cmd", "scripts"),
        ("frontend/src/App.tsx", "sources"),
        ("backend/services/audio.py", "sources"),
        ("tools/bazel/sh_rules.bzl", "sources"),
        ("package.json", "config"),
        ("conf/app.yaml", "config"),
        (".gitignore", "config"),
        (".gitattributes", "config"),
        (".env.example", "config"),
        (".gitkeep", "config"),
        ("frontend/.npmrc", "config"),
        ("media/clip.mp4", "data"),
        ("media/icon.svg", "data"),
        ("media/font.woff2", "data"),
        ("notes.unknown", "data"),
        ("Makefile", "data"),
    ],
)
def test_classify_routes_files_into_buckets(rel: str, expected: str) -> None:
    assert build_files.classify(rel) == expected


def test_test_directory_names_only_count_as_directories() -> None:
    assert build_files.classify("backend/tests.py") == "sources"
    assert build_files.classify("backend/tests/test_agent.py") == "tests"


def test_bucket_precedence_is_first_match_wins() -> None:
    assert build_files.classify("assets/notes.md") == "assets"
    assert build_files.classify("assets/tool.sh") == "assets"
    assert build_files.classify("docs/bootstrap.sh") == "scripts"
    assert build_files.classify("tests/notes.md") == "tests"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("clean.py", False),
        ("UPPER-1_x.py", False),
        ("with space.py", True),
        ("with\ttab.py", True),
        ("hash#tag.py", True),
        ("pct%2f.py", True),
        ("quote'.py", True),
        ("paren(.py", True),
        ("dollar$.py", True),
        ("unicode-caf\u00e9.py", True),
    ],
)
def test_name_is_illegal(base: str, expected: bool) -> None:
    assert build_files.name_is_illegal(base) is expected


def test_needs_glob_explains_unexpressible_filenames() -> None:
    assert build_files.needs_glob(["pkg/a.py", "pkg/b.py"]) is None
    reason = build_files.needs_glob(["pkg/a.py", "pkg/my file.py"])
    assert reason is not None
    assert "Bazel label" in reason


# --- rendering --------------------------------------------------------------


def test_render_names_every_bucket_in_declared_order() -> None:
    text = build_files.render(
        "pkg",
        [
            "pkg/z.md",
            "pkg/a.py",
            "pkg/b.sh",
            "pkg/c.json",
            "pkg/d.png",
            "pkg/test_a.py",
        ],
        None,
    )
    assert text.startswith(HEADER_TEXT)
    assert 'package(default_visibility = ["//visibility:public"])\n' in text
    assert "glob(" not in text
    names = [line.split('"')[1] for line in text.splitlines() if "name = " in line]
    assert names == ["tests", "docs", "scripts", "config", "sources", "data"]
    assert '        "z.md",\n' in text
    assert '        "test_a.py",\n' in text
    assert text.count('filegroup(\n') == 6


def test_render_sorts_the_files_inside_a_bucket() -> None:
    text = build_files.render("pkg", ["pkg/b.py", "pkg/a.py", "pkg/c.py"], None)
    srcs = [line.strip('", ') for line in text.splitlines() if line.startswith('        "')]
    assert srcs == ["a.py", "b.py", "c.py"]


def test_render_falls_back_to_a_policy_commented_glob() -> None:
    reason = "filenames here are not expressible as Bazel label literals"
    text = build_files.render("weird", ["weird/my file.txt"], reason)
    assert text.startswith(HEADER_TEXT)
    assert "# glob-policy: %s\n" % reason in text
    assert '    name = "payload",\n' in text
    assert "    srcs = glob(\n" in text
    assert '        ["**/*"],\n' in text
    assert "        allow_empty = False,\n" in text
    assert '        exclude = ["BUILD.bazel"],\n' in text
    assert text.count("filegroup(") == 1


# --- package grouping -------------------------------------------------------


@pytest.mark.parametrize(
    "dirpath", [".", "tools/bazel", "tools/bazel/lanes", "tools/bazel/tests"]
)
def test_is_hand_authored_covers_the_root_and_the_tools_subtree(dirpath: str) -> None:
    assert build_files.is_hand_authored(dirpath) is True


@pytest.mark.parametrize("dirpath", ["", "tools", "backend", "tools/bazelish"])
def test_is_hand_authored_rejects_other_directories(dirpath: str) -> None:
    assert build_files.is_hand_authored(dirpath) is False


@pytest.mark.parametrize(
    "rel",
    [
        "MODULE.bazel",
        "MODULE.bazel.lock",
        ".bazelrc",
        ".bazelignore",
        "REPO.bazel",
        "WORKSPACE",
        "pkg/BUILD",
        "pkg/BUILD.bazel",
    ],
)
def test_is_control_plane_recognises_build_inputs(rel: str) -> None:
    assert build_files.is_control_plane(rel) is True


@pytest.mark.parametrize("rel", ["pkg/a.py", "README.md", "pkg/bazel.bazel"])
def test_is_control_plane_rejects_ordinary_files(rel: str) -> None:
    assert build_files.is_control_plane(rel) is False


def test_packages_skips_control_plane_and_ledgered_files() -> None:
    tracked = [
        "MODULE.bazel",
        "pkg/BUILD.bazel",
        "pkg/a.py",
        "docs/note.md",
        "README.md",
    ]
    ledger = [("docs/*.md", "owned by the docs target")]
    pkgs = build_files.packages(tracked, ledger)
    assert sorted(pkgs) == [".", "pkg"]
    assert pkgs["."] == (["README.md"], None)
    assert pkgs["pkg"] == (["pkg/a.py"], None)


def test_packages_sorts_members_and_groups_the_root_package() -> None:
    pkgs = build_files.packages(["pkg/b.py", "pkg/a.py", "top.md"], [])
    assert pkgs["pkg"][0] == ["pkg/a.py", "pkg/b.py"]
    assert pkgs["."] == (["top.md"], None)


def test_packages_gives_payload_directories_a_tree_reason() -> None:
    pkgs = build_files.packages(["frontend/public/i.png"], [])
    reason = pkgs["frontend/public"][1]
    assert reason is not None
    assert "tree itself is the contract" in reason


def test_packages_gives_illegal_filenames_a_label_reason() -> None:
    pkgs = build_files.packages(["weird/my file.txt"], [])
    reason = pkgs["weird"][1]
    assert reason is not None
    assert "label literals" in reason


# --- ledger -----------------------------------------------------------------


def test_read_ledger_parses_comments_tabs_and_wide_gaps(tmp_path) -> None:
    ledger = tmp_path / "bazel-unowned.txt"
    ledger.write_text(
        "# reasons\n"
        "\n"
        "   \n"
        "   # indented comment\n"
        "docs/*.md\towned by the docs target\n"
        "assets/logo.png   shipped by the web bundle\n"
        "stray.txt\n",
        encoding="utf-8",
    )
    assert build_files.read_ledger(ledger) == [
        ("docs/*.md", "owned by the docs target"),
        ("assets/logo.png", "shipped by the web bundle"),
        ("stray.txt", ""),
    ]


def test_read_ledger_tolerates_a_missing_file(tmp_path) -> None:
    assert build_files.read_ledger(tmp_path / "absent.txt") == []


def test_ledgerled_reports_the_reason_or_none() -> None:
    ledger = [("docs/*.md", "a reason"), ("pkg/a.py", "")]
    assert build_files.ledgerled("docs/note.md", ledger) == "a reason"
    assert build_files.ledgerled("pkg/a.py", ledger) == ""
    assert build_files.ledgerled("pkg/b.py", ledger) is None


# --- the run helper ---------------------------------------------------------


def test_run_returns_stdout_on_success(git_repo) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    assert build_files.run(["git", "ls-files"], str(repo)).strip() == "pkg/a.py"


def test_run_exits_2_and_echoes_stderr_on_failure(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        build_files.run(["git", "definitely-not-a-subcommand"], str(tmp_path))
    assert raised.value.code == 2
    assert capsys.readouterr().err.strip()


# --- main: writing and checking ---------------------------------------------


def test_write_creates_one_build_file_per_package(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n", "pkg/test_a.py": "print()\n"})
    install_run(patch_run, ["pkg/a.py", "pkg/test_a.py"])
    assert invoke(repo, "--write") == 0
    text = (repo / "pkg" / "BUILD.bazel").read_text(encoding="utf-8")
    assert text.startswith(HEADER_TEXT)
    assert '    name = "tests",' in text
    assert '    name = "sources",' in text
    assert "summary: 1 package(s), 1 wrote, 0 stale" in capsys.readouterr().out


def test_check_reports_missing_build_files_as_drift(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"pkg/a.py": "print()\n", "docs/note.md": "# n\n"})
    install_run(patch_run, ["pkg/a.py", "docs/note.md"])
    assert invoke(repo, "--check") == 1
    out = capsys.readouterr().out
    assert "DRIFT pkg/BUILD.bazel" in out
    assert "DRIFT docs/BUILD.bazel" in out
    assert "summary: 2 package(s), 2 would write, 0 stale" in out
    assert not (repo / "pkg" / "BUILD.bazel").exists()


def test_check_is_clean_once_the_files_match_a_render(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    manifest = ["pkg/a.py"]
    install_run(patch_run, manifest)
    assert invoke(repo, "--write") == 0
    capsys.readouterr()
    assert invoke(repo, "--check") == 0
    assert "summary: 1 package(s), 0 would write, 0 stale" in capsys.readouterr().out


def test_hand_edited_build_file_is_drift_and_write_restores_it(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    hand = repo / "pkg" / "BUILD.bazel"
    hand.write_text("# hand written\n", encoding="utf-8")
    install_run(patch_run, ["pkg/a.py"], untracked=["pkg/BUILD.bazel"])
    assert invoke(repo, "--check") == 1
    assert "DRIFT pkg/BUILD.bazel" in capsys.readouterr().out
    assert invoke(repo, "--write") == 0
    assert hand.read_text(encoding="utf-8").startswith(HEADER_TEXT)


def test_write_removes_a_stale_tracked_build_file(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"pkg/a.py": "print()\n", "old/BUILD.bazel": "# obsolete\n"})
    install_run(patch_run, ["pkg/a.py", "old/BUILD.bazel"])
    assert invoke(repo, "--write") == 0
    assert "STALE old/BUILD.bazel" in capsys.readouterr().out
    assert not (repo / "old" / "BUILD.bazel").exists()


def test_check_keeps_a_stale_untracked_build_file_for_a_human(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    leftover = repo / "leftover" / "BUILD.bazel"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_text("# obsolete\n", encoding="utf-8")
    install_run(patch_run, ["pkg/a.py"], untracked=["leftover/BUILD.bazel"])
    assert invoke(repo, "--check") == 1
    assert "STALE leftover/BUILD.bazel" in capsys.readouterr().out
    assert leftover.exists()


def test_write_removes_a_stale_untracked_build_file(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    leftover = repo / "leftover" / "BUILD.bazel"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_text("# obsolete\n", encoding="utf-8")
    install_run(patch_run, ["pkg/a.py"], untracked=["leftover/BUILD.bazel"])
    assert invoke(repo, "--write") == 0
    assert not leftover.exists()


def test_hand_authored_directories_are_never_generated(
    git_repo, patch_run, capsys
) -> None:
    files = {
        "README.md": "# vFlo\n",
        "MODULE.bazel": "module(name = \"vlo\")\n",
        "tools/bazel/gate.py": "print()\n",
        "tools/bazel/lanes/run.sh": "#!/bin/bash\n",
        "tools/bazel/BUILD.bazel": "# hand authored\n",
    }
    repo = git_repo(files)
    root_build = repo / "BUILD.bazel"
    root_build.write_text("# hand authored\n", encoding="utf-8")
    install_run(
        patch_run,
        [
            "README.md",
            "MODULE.bazel",
            "tools/bazel/gate.py",
            "tools/bazel/lanes/run.sh",
            "tools/bazel/BUILD.bazel",
        ],
        untracked=["BUILD.bazel"],
    )
    assert invoke(repo, "--write") == 0
    assert "summary: 0 package(s), 0 wrote, 0 stale" in capsys.readouterr().out
    assert root_build.read_text(encoding="utf-8") == "# hand authored\n"
    assert (repo / "tools" / "bazel" / "BUILD.bazel").read_text(
        encoding="utf-8"
    ) == "# hand authored\n"


def test_root_files_alone_produce_no_root_build_file(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"README.md": "# vFlo\n", "pkg/a.py": "print()\n"})
    install_run(patch_run, ["README.md", "pkg/a.py"])
    assert invoke(repo, "--write") == 0
    assert not (repo / "BUILD.bazel").exists()
    assert (repo / "pkg" / "BUILD.bazel").exists()
    assert "summary: 1 package(s)" in capsys.readouterr().out


# --- main: glob fallback ----------------------------------------------------


@pytest.mark.parametrize(
    "rel", ["frontend/public/logo.png", "backend/assets/clip.mp4"]
)
def test_payload_directories_render_a_policy_commented_glob(
    git_repo, patch_run, rel: str
) -> None:
    repo = git_repo({rel: "payload\n"})
    install_run(patch_run, [rel])
    assert invoke(repo, "--write") == 0
    text = (repo / Path(rel).parent / "BUILD.bazel").read_text(encoding="utf-8")
    assert "# glob-policy:" in text
    assert '    name = "payload",' in text
    assert '        exclude = ["BUILD.bazel"],' in text


def test_unexpressible_filename_falls_back_to_a_glob(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"weird/my file.txt": "text\n"})
    install_run(patch_run, ["weird/my file.txt"])
    assert invoke(repo, "--write") == 0
    text = (repo / "weird" / "BUILD.bazel").read_text(encoding="utf-8")
    assert "# glob-policy:" in text
    assert "label literals" in text
    assert '"my file.txt"' not in text


# --- main: ledger gate ------------------------------------------------------


def test_ledger_entries_keep_files_out_of_the_graph(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n", "docs/note.md": "# n\n"})
    (repo / "bazel-unowned.txt").write_text(
        "# excluded on purpose\ndocs/*.md\towned by the docs target\n", encoding="utf-8"
    )
    install_run(patch_run, ["pkg/a.py", "docs/note.md", "bazel-unowned.txt"])
    assert invoke(repo, "--write") == 0
    assert (repo / "pkg" / "BUILD.bazel").exists()
    assert not (repo / "docs" / "BUILD.bazel").exists()
    assert "summary: 1 package(s)" in capsys.readouterr().out


def test_ledger_entry_without_a_reason_fails_before_rendering(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# n\n"})
    (repo / "bazel-unowned.txt").write_text("docs/*.md\n", encoding="utf-8")
    install_run(patch_run, ["docs/note.md", "bazel-unowned.txt"])
    assert invoke(repo, "--write") == 1
    out = capsys.readouterr().out
    assert "LEDGER docs/*.md  (entry carries no reason)" in out
    assert "summary:" not in out
    assert not (repo / "docs" / "BUILD.bazel").exists()


def test_ledger_path_may_be_absolute(
    git_repo, patch_run, tmp_path, capsys
) -> None:
    repo = git_repo({"docs/note.md": "# n\n"})
    outside = tmp_path / "outside-ledger.txt"
    outside.write_text("docs/*.md\tkept outside the repo\n", encoding="utf-8")
    install_run(patch_run, ["docs/note.md"])
    assert invoke(repo, "--write", "--ledger", str(outside)) == 0
    assert "summary: 0 package(s)" in capsys.readouterr().out


def test_empty_manifest_is_an_environment_error(git_repo, patch_run, capsys) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    install_run(patch_run, [])
    assert invoke(repo, "--check") == 2
    assert "error: git ls-files returned nothing" in capsys.readouterr().err


# --- main: reporting --------------------------------------------------------


def test_json_report_lists_drift_stale_and_written(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n", "old/BUILD.bazel": "# obsolete\n"})
    install_run(patch_run, ["pkg/a.py", "old/BUILD.bazel"])
    assert invoke(repo, "--write", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packages"] == 1
    assert payload["drift"] == ["pkg/BUILD.bazel"]
    assert payload["written"] == ["pkg/BUILD.bazel"]
    assert payload["stale"] == ["old/BUILD.bazel"]


def test_json_report_is_clean_when_everything_matches(
    git_repo, patch_run, capsys
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    install_run(patch_run, ["pkg/a.py"])
    assert invoke(repo, "--write") == 0
    capsys.readouterr()
    assert invoke(repo, "--check", "--json") == 0
    assert json.loads(capsys.readouterr().out) == {
        "packages": 1,
        "drift": [],
        "stale": [],
        "written": [],
    }


# --- CLI entry point --------------------------------------------------------


def test_module_main_exits_with_the_status_code(
    git_repo, patch_subprocess_run, tmp_path
) -> None:
    repo = git_repo({"pkg/a.py": "print()\n"})
    patch_subprocess_run()
    script = Path(build_files.__file__).resolve()
    sys.argv = [str(script), "--root", str(repo), "--check"]
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(script), run_name="__main__")
    assert raised.value.code == 1
    assert (repo / "pkg" / "BUILD.bazel").exists() is False
