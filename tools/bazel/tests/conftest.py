"""Shared fixtures for the tools/bazel gate-script tests.

The four gate scripts are plain modules that shell out to ``git`` and
``bazelisk``. Every test drives them against a throwaway repository created in
``tmp_path`` and, where a script would otherwise start a Bazel server, swaps the
module-level ``run`` helper for :class:`RunRecorder`. Nothing here may read the
real checkout or invoke ``bazelisk``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import pytest

# The scripts under test are top-level modules in tools/bazel (no package), so
# the directory itself has to be importable while pytest runs from tools/bazel.
TOOLS_BAZEL_DIR = Path(__file__).resolve().parents[1]
BAZEL_SCRIPTS = {
    "build_files": TOOLS_BAZEL_DIR / "build_files.py",
    "docs_gate": TOOLS_BAZEL_DIR / "docs_gate.py",
    "glob_budget": TOOLS_BAZEL_DIR / "glob_budget.py",
    "owned_files_check": TOOLS_BAZEL_DIR / "owned_files_check.py",
}
if str(TOOLS_BAZEL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_BAZEL_DIR))


@pytest.fixture(autouse=True)
def hermetic_git_env(monkeypatch, tmp_path) -> None:
    """Pin the git environment so no test sees a global config or a parent repo.

    The scripts inherit this process environment when they call git, so the
    isolation has to live in ``os.environ`` rather than in per-call kwargs.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Gate Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "gate-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Gate Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "gate-test@example.invalid")
    for leaked in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE"):
        monkeypatch.delenv(leaked, raising=False)


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git inside ``repo`` with an identity that needs no user config."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


class RunRecorder:
    """Stand-in for a gate script's ``run(cmd, cwd)`` helper.

    ``git`` commands fall through to the real binary so the tests exercise the
    actual ``git ls-files`` parsing against a scratch repo; ``bazelisk``
    commands are answered from ``bazelisk_stdout`` and never start a server.
    A command whose joined text appears in ``fail_commands`` reproduces the
    helper's non-zero exit path.
    """

    def __init__(
        self,
        bazelisk_stdout: str = "",
        git_stdout: Optional[Mapping[str, str]] = None,
        fail_commands: Iterable[str] = (),
    ) -> None:
        self.bazelisk_stdout = bazelisk_stdout
        self.git_stdout: Dict[str, str] = dict(git_stdout or {})
        self.fail_commands: List[str] = list(fail_commands)
        self.calls: List[List[str]] = []

    def __call__(self, cmd: Sequence[str], cwd: str) -> str:
        argv = list(cmd)
        self.calls.append(argv)
        joined = " ".join(argv)
        if any(needle in joined for needle in self.fail_commands):
            sys.stderr.write("simulated failure for: %s\n" % joined)
            raise SystemExit(2)
        for needle, stdout in self.git_stdout.items():
            if needle in joined:
                return stdout
        if argv and argv[0] == "bazelisk":
            return self.bazelisk_stdout
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr[-4000:])
            raise SystemExit(2)
        return proc.stdout

    def commands(self) -> List[str]:
        """The joined command lines seen so far, for assertions."""
        return [" ".join(argv) for argv in self.calls]


@pytest.fixture
def patch_run(monkeypatch):
    """Install a :class:`RunRecorder` over a gate module's ``run`` helper."""

    def _install(module, **kwargs) -> RunRecorder:
        recorder = RunRecorder(**kwargs)
        monkeypatch.setattr(module, "run", recorder)
        return recorder

    return _install


@pytest.fixture
def git_repo(tmp_path):
    """Factory that builds a committed scratch git repository.

    ``files`` maps a repo-relative path to its text content; the manifest the
    scripts see is whatever this writes, so a test never scans the real repo.
    """

    def _make(files: Mapping[str, str], *, name: str = "repo") -> Path:
        repo = tmp_path / name
        repo.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        run_git(repo, "init", "-q")
        run_git(repo, "add", "--all")
        run_git(repo, "commit", "-q", "-m", "initial", "--allow-empty")
        return repo

    return _make


_REAL_SUBPROCESS_RUN = subprocess.run


@pytest.fixture(autouse=True)
def no_bazel_server(monkeypatch) -> None:
    """Fail fast if any test reaches for a real Bazel binary.

    Every ``bazelisk`` call in the scripts goes through ``subprocess``, so this
    is the backstop behind the ``run``/``subprocess.run`` doubles.
    """
    real_popen = subprocess.Popen

    def _guard(cmd, *args, **kwargs):
        argv = cmd if isinstance(cmd, (list, tuple)) else str(cmd).split()
        if argv and os.path.basename(str(argv[0])) in ("bazel", "bazelisk"):
            raise AssertionError("a test tried to exec %s" % argv[0])
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _guard)


@pytest.fixture
def patch_subprocess_run(monkeypatch):
    """Patch the stdlib ``subprocess.run`` so a re-executed script stays offline.

    ``runpy.run_path`` re-runs a script's module body, which rebinds its own
    ``run`` helper; patching the stdlib entry point is the only way to keep a
    ``__main__`` test from reaching for ``bazelisk``.
    """

    def _install(bazelisk_stdout: str = "") -> None:
        def _fake(cmd, *args, **kwargs):
            argv = cmd if isinstance(cmd, (list, tuple)) else str(cmd).split()
            if any("bazelisk" in part for part in argv):
                if not bazelisk_stdout:
                    return subprocess.CompletedProcess(cmd, 1, "", "no bazel server\n")
                return subprocess.CompletedProcess(cmd, 0, bazelisk_stdout, "")
            return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _fake)

    return _install


@pytest.fixture
def write_cli_args(monkeypatch):
    """Replace ``sys.argv`` so a script's ``main()`` parses the given options."""

    def _set(script_name: str, *args: str) -> None:
        monkeypatch.setattr(sys, "argv", [script_name, *args])

    return _set
