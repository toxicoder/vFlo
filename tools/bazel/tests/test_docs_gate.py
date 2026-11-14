"""Tests for tools/bazel/docs_gate.py.

Three checks share one contract: a person reading the docs must only ever be
told to run a ``//target``. The tests keep a clean repository on hand and break
one rule at a time, so a passing check never hides behind another.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

import docs_gate

QUERY = "//:gate\n//:fast\n//tools/bazel:docs_gate\n//tools/bazel:doctor\n"

CLEAN_FILES: Dict[str, str] = {
    "README.md": "# vFlo\n\nRun `bazelisk run //:gate` before pushing.\n",
    "CONTRIBUTING.md": (
        "# Contributing\n\nUse //:gate for everything and //:fast while iterating.\n"
    ),
    "AGENTS.md": "# Style\n\nThe full lane is `//:gate`.\n",
    "docs/style.md": "See //tools/bazel:docs_gate for the rules.\n",
    "tools/bazel/lanes/gate.sh": "#!/bin/bash\n# lane: bazelisk test //:gate\n",
    "frontend/src/app.tsx": "export const x = 1;\n",
}


def invoke(root: Path, *extra: str) -> int:
    """Run ``docs_gate.main()`` with ``--root`` pinned to ``root``."""
    sys.argv = ["docs_gate.py", "--root", str(root), *extra]
    return docs_gate.main()


def docs_repo(git_repo, overrides: Optional[Dict[str, str]] = None) -> Path:
    """Materialise the clean documentation surface with one file perturbed."""
    files = dict(CLEAN_FILES)
    files.update(overrides or {})
    return git_repo(files)


def tracked_lines(line: str) -> str:
    """The docs/style.md body with ``line`` appended."""
    return "See //tools/bazel:docs_gate for the rules.\n" + line + "\n"


# --- manifest filters -------------------------------------------------------


def _manifest(*rels: str) -> str:
    return "\0".join(rels)


def test_tracked_docs_keeps_only_markdown(patch_run) -> None:
    patch_run(
        docs_gate,
        git_stdout={
            "ls-files": _manifest(
                "README.md", "guide.mdx", "notes.txt", "tools/bazel/gate.py"
            )
        },
    )
    assert list(docs_gate.tracked_docs(Path("/repo"))) == ["README.md", "guide.mdx"]


def test_tracked_text_adds_the_lane_sources(patch_run) -> None:
    patch_run(
        docs_gate,
        git_stdout={
            "ls-files": _manifest(
                "docs/guide.md",
                "tools/bazel/gate.py",
                "tools/bazel/lanes/gate.sh",
                "tools/bazel/sh_rules.bzl",
                "tools/bazel/BUILD.bazel",
                "frontend/app.tsx",
                "backend/services/gate.py",
            )
        },
    )
    assert list(docs_gate.tracked_text(Path("/repo"))) == [
        "docs/guide.md",
        "tools/bazel/gate.py",
        "tools/bazel/lanes/gate.sh",
        "tools/bazel/sh_rules.bzl",
    ]


def test_tracked_text_skips_test_fixtures(patch_run) -> None:
    patch_run(
        docs_gate,
        git_stdout={
            "ls-files": _manifest(
                "docs/guide.md",
                "tools/bazel/gate.py",
                "tools/bazel/tests/test_gate.py",
                "tools/bazel/__tests__/gate_check.py",
                "frontend/src/__tests__/App.test.tsx",
            )
        },
    )
    assert list(docs_gate.tracked_text(Path("/repo"))) == [
        "docs/guide.md",
        "tools/bazel/gate.py",
    ]


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("tools/bazel/tests/test_gate.py", True),
        ("tools/bazel/__tests__/gate.py", True),
        ("test_gate.py", True),
        ("tools/bazel/gate.test.py", True),
        ("tools/bazel/gate_test.py", True),
        ("tools/bazel/docs_gate.py", False),
        ("tools/bazel/tests.py", False),
        ("docs/guide.md", False),
    ],
)
def test_is_test_fixture_recognises_fixture_shapes(rel: str, expected: bool) -> None:
    assert docs_gate.is_test_fixture(rel) is expected


def test_known_labels_strips_blank_lines(patch_run) -> None:
    patch_run(docs_gate, bazelisk_stdout="  //:gate  \n\n   \n//:fast\n")
    assert docs_gate.known_labels(Path("/repo")) == {"//:gate", "//:fast"}


# --- the run helper ---------------------------------------------------------


def test_run_returns_stdout_on_success(git_repo) -> None:
    repo = git_repo({"README.md": "# vFlo\n"})
    assert docs_gate.run(["git", "ls-files"], str(repo)).strip() == "README.md"


def test_run_exits_2_and_echoes_stderr_on_failure(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        docs_gate.run(["git", "definitely-not-a-subcommand"], str(tmp_path))
    assert raised.value.code == 2
    assert capsys.readouterr().err.strip()


# --- the clean surface ------------------------------------------------------


def test_clean_documentation_passes(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(git_repo)
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0
    assert "summary: 0 docs violation(s)" in capsys.readouterr().out


def test_json_output_is_an_empty_list_when_clean(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(git_repo)
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo, "--json") == 0
    assert json.loads(capsys.readouterr().out) == {"violations": []}


# --- check 1: never run a script by path ------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Run bash tools/bazel/lanes/gate.sh to debug.",
        "Run sh tools/bazel/lanes/gate.sh to debug.",
        "Run source tools/bazel/lanes/common.sh first.",
        "Run python3 tools/bazel/gate.py to debug.",
        "Run python tools/bazel/gate.py to debug.",
        "Run node tools/bazel/tool.mjs to debug.",
        "Run nohup tools/bazel/lanes/gate.sh in the background.",
        "Run bash ./tools/bazel/lanes/gate.sh to debug.",
        # A direct invocation is a run-by-path even with no interpreter in front.
        "Run ./tools/bazel/lanes/gate.sh to debug.",
        "Run ./tools/bazel/lanes/gate.sh --help.",
        "Debug with `./tools/bazel/gate.py` instead.",
        "Copy (./tools/bazel/tool.mjs) into the payload.",
    ],
)
def test_running_a_lane_script_by_path_is_a_violation(
    git_repo, patch_run, capsys, line: str
) -> None:
    repo = docs_repo(git_repo, {"docs/style.md": tracked_lines(line)})
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "DOCS docs/style.md:2" in out
    assert "tells a person to run a gate script by path" in out


@pytest.mark.parametrize(
    "line",
    [
        "The lane script lives at tools/bazel/lanes/gate.sh.",
        "Run bash scripts/dev.sh to start the dev server.",
        "Run ./frontend/node_modules/.bin/vitest --run.",
        "tools/bazel/gate.py is the source, //tools/bazel:docs_gate is the target.",
        # Without a run keyword and without the leading ./, the path is prose.
        "Run tools/bazel/lanes/gate.sh once by hand.",
        "The lane sources live under tools/bazel/ and are named as targets.",
    ],
)
def test_naming_a_script_without_running_it_is_allowed(
    git_repo, patch_run, capsys, line: str
) -> None:
    repo = docs_repo(git_repo, {"docs/style.md": tracked_lines(line)})
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0
    assert "summary: 0 docs violation(s)" in capsys.readouterr().out


def test_unreadable_doc_is_an_environment_error(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(git_repo)
    (repo / "README.md").unlink()
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 2
    assert "error: cannot read README.md:" in capsys.readouterr().err


# --- check 2: every cited label resolves ------------------------------------


def test_citing_an_unresolvable_label_is_a_violation(
    git_repo, patch_run, capsys
) -> None:
    repo = docs_repo(
        git_repo, {"docs/style.md": "Run `bazelisk test //tools/bazel:nope`.\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "DOCS docs/style.md:1" in out
    assert "cites //tools/bazel:nope, which bazelisk query cannot resolve" in out


def test_labels_inside_a_longer_path_are_not_cited(git_repo, patch_run) -> None:
    repo = docs_repo(
        git_repo, {"docs/style.md": "The file a//pkg:t and teleglob//pkg:t are fine.\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0


def test_builtin_visibility_labels_are_not_required_to_resolve(
    git_repo, patch_run
) -> None:
    repo = docs_repo(
        git_repo,
        {"docs/style.md": "Use `//visibility:public`, never `//visibility:private`.\n"},
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0


def test_a_url_is_not_read_as_a_label(git_repo, patch_run) -> None:
    repo = docs_repo(
        git_repo,
        {"docs/style.md": "The search box hits http://127.0.0.1:8188/search.\n"},
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0


def test_a_sentence_final_period_is_not_part_of_the_target(
    git_repo, patch_run
) -> None:
    repo = docs_repo(
        git_repo, {"docs/style.md": "The doctor lane is //tools/bazel:doctor.\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0


def test_a_sentence_final_period_is_kept_out_of_the_reported_label(
    git_repo, patch_run, capsys
) -> None:
    repo = docs_repo(git_repo, {"docs/style.md": "Run //:gone.\n"})
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "cites //:gone, which bazelisk query cannot resolve" in out
    assert "cites //:gone., which" not in out


def test_a_fixture_may_cite_a_label_that_does_not_resolve(
    git_repo, patch_run
) -> None:
    """The gate's own fixtures assert against dead labels, so they stay code."""
    repo = docs_repo(
        git_repo,
        {
            "tools/bazel/tests/test_gate.py": (
                "FIXTURE = {'docs/style.md': 'Run //:gone.\\n'}\n"
            )
        },
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 0


def test_a_non_fixture_source_still_has_to_resolve_its_labels(
    git_repo, patch_run, capsys
) -> None:
    """The exemption follows fixtures only, never the whole directory."""
    repo = docs_repo(
        git_repo, {"tools/bazel/gate_notes.py": "# see //:gone\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    assert "cites //:gone, which bazelisk query cannot resolve" in (
        capsys.readouterr().out
    )


def test_labels_are_checked_in_lane_sources_too(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(
        git_repo,
        {"tools/bazel/lanes/gate.sh": "#!/bin/bash\n# see //:nonexistent\n"},
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    assert "DOCS tools/bazel/lanes/gate.sh:2" in capsys.readouterr().out


def test_unreadable_lane_source_is_an_environment_error(
    git_repo, patch_run, capsys
) -> None:
    repo = docs_repo(git_repo)
    (repo / "tools" / "bazel" / "lanes" / "gate.sh").unlink()
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 2
    err = capsys.readouterr().err
    assert "error: cannot read tools/bazel/lanes/gate.sh:" in err


# --- check 3: entry points stay documented ----------------------------------


@pytest.mark.parametrize("missing", ["README.md", "CONTRIBUTING.md", "AGENTS.md"])
def test_missing_entry_point_doc_is_a_violation(
    git_repo, patch_run, capsys, missing: str
) -> None:
    files = dict(CLEAN_FILES)
    del files[missing]
    repo = git_repo(files)
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "DOCS %s:0  entry-point doc is missing" % missing in out


def test_entry_point_doc_that_omits_a_target_is_a_violation(
    git_repo, patch_run, capsys
) -> None:
    repo = docs_repo(git_repo, {"AGENTS.md": "# Style\n\nNo entry point named here.\n"})
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    assert "does not document //:gate as an entry point" in capsys.readouterr().out


def test_contributing_needs_both_entry_points(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(
        git_repo, {"CONTRIBUTING.md": "# Contributing\n\nUse //:gate.\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    assert "does not document //:fast as an entry point" in capsys.readouterr().out


# --- reporting --------------------------------------------------------------


def test_json_output_lists_every_violation(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(
        git_repo, {"docs/style.md": "Run `bazelisk test //:gone`.\n"}
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo, "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["violations"] == [
        {
            "file": "docs/style.md",
            "line": 1,
            "problem": "cites //:gone, which bazelisk query cannot resolve",
            "text": "Run `bazelisk test //:gone`.",
        }
    ]


def test_several_checks_report_together(git_repo, patch_run, capsys) -> None:
    repo = docs_repo(
        git_repo,
        {
            "docs/style.md": "Run bash tools/bazel/lanes/gate.sh and //:gone.\n",
            "AGENTS.md": "# Style\n",
        },
    )
    patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 1
    out = capsys.readouterr().out
    assert "summary: 3 docs violation(s)" in out
    assert out.count("DOCS ") == 3


def test_the_first_failing_check_short_circuits_the_run(
    git_repo, patch_run, capsys
) -> None:
    repo = docs_repo(git_repo)
    (repo / "docs" / "style.md").unlink()
    recorder = patch_run(docs_gate, bazelisk_stdout=QUERY)
    assert invoke(repo) == 2
    assert "error: cannot read docs/style.md:" in capsys.readouterr().err
    assert [cmd for cmd in recorder.commands() if "bazelisk" in cmd] == []


# --- CLI entry point --------------------------------------------------------


def test_module_main_exits_with_the_status_code(
    git_repo, patch_subprocess_run, tmp_path
) -> None:
    repo = docs_repo(git_repo)
    patch_subprocess_run(bazelisk_stdout=QUERY)
    script = Path(docs_gate.__file__).resolve()
    sys.argv = [str(script), "--root", str(repo)]
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(script), run_name="__main__")
    assert raised.value.code == 0
