#!/usr/bin/env python3
"""Gate: the prose and the CLI text speak in targets.

A Bazel-first repo is only usable if a human never needs a script path. This
checks three things over the documentation surfaces:

  1. No doc tells a person to run a checked-in gate script by path. The target
     is the supported spelling, so `bazelisk test //tools/bazel:owned_files_check`
     is right and `bash tools/bazel/lanes/owned_files_check.sh` is not.
  2. Every target a doc or help string cites actually resolves. A doc that
     drifts from the build graph is worse than no doc.
  3. The aggregate entry points stay documented.

Usage:
    docs_gate.py [--root DIR] [--json]

Exit codes: 0 clean, 1 violation, 2 usage/environment error.
"""

import argparse
import json
import os
import re
import subprocess
import sys

DOC_GLOBS = (".md", ".mdx")

# Where the entry points must stay named, relative to the root.
ENTRY_POINT_DOCS = {
    "README.md": ("//:gate",),
    "CONTRIBUTING.md": ("//:gate", "//:fast"),
    "AGENTS.md": ("//:gate",),
}

# Docs may name a script when the script is the interface (the installer, the
# npm entry points). Anything under tools/bazel must be named as a target.
FORBIDDEN_PATH_PREFIXES = ("tools/bazel/",)

# A label's package and name. The lookbehind keeps a URL out of the net: without
# the `:` term, `http://127.0.0.1:8188` reads as package `127.0.0.1`, name `8188`.
LABEL_RE = re.compile(r"(?<![\w/:])//([A-Za-z0-9_./+-]*):([A-Za-z0-9_.+-]+)")

# Bazel's own built-in visibility labels. They are not queryable targets, so
# resolving them against `bazelisk query` would only teach people to stop
# writing `//visibility:public` in a BUILD file.
BUILTIN_LABELS = {"//visibility:public", "//visibility:private"}
RUN_BY_PATH_RE = re.compile(
    r"(?:^|[\s`'\"(=])(?:bash|sh|source|python3?|node|nohup|\./)\s+\S*"
    r"(?:tools/bazel/)[A-Za-z0-9_./-]+\.(?:sh|py|mjs|js)\b")


def run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(2)
    return proc.stdout


def tracked_docs(root):
    out = run(["git", "ls-files", "-z"], root)
    for rel in (p for p in out.split("\0") if p):
        if rel.endswith(DOC_GLOBS):
            yield rel


def tracked_text(root):
    """Docs plus the .py/.sh/.bzl text a person reads when a lane fails."""
    out = run(["git", "ls-files", "-z"], root)
    for rel in (p for p in out.split("\0") if p):
        if rel.endswith(DOC_GLOBS) or rel.startswith("tools/bazel/") and \
           rel.endswith((".py", ".sh", ".bzl")):
            yield rel


def known_labels(root):
    out = run(["bazelisk", "query", "//...", "--output=label"], root)
    return {line.strip() for line in out.splitlines() if line.strip()}


def check_forbidden_paths(root, offenders):
    for rel in tracked_docs(root):
        path = os.path.join(root, rel)
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except OSError as exc:
            sys.stderr.write("error: cannot read %s: %s\n" % (rel, exc))
            return 2
        for number, line in enumerate(lines, 1):
            match = RUN_BY_PATH_RE.search(line)
            if match:
                offenders.append({
                    "file": rel, "line": number,
                    "problem": "tells a person to run a gate script by path; "
                               "name the //target instead",
                    "text": line.strip()[:100],
                })
    return 0


def check_labels(root, offenders):
    labels = known_labels(root)
    for rel in tracked_text(root):
        path = os.path.join(root, rel)
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            sys.stderr.write("error: cannot read %s: %s\n" % (rel, exc))
            return 2
        for number, line in enumerate(text.splitlines(), 1):
            for pkg, name in LABEL_RE.findall(line):
                label = "//%s:%s" % (pkg, name)
                if label in BUILTIN_LABELS:
                    continue
                if label not in labels:
                    offenders.append({
                        "file": rel, "line": number,
                        "problem": "cites %s, which bazelisk query cannot resolve" % label,
                        "text": line.strip()[:100],
                    })
    return 0


def check_entry_points(root, offenders):
    for rel, required in ENTRY_POINT_DOCS.items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            offenders.append({"file": rel, "line": 0,
                              "problem": "entry-point doc is missing", "text": ""})
            continue
        text = open(path, encoding="utf-8").read()
        for label in required:
            if label not in text:
                offenders.append({
                    "file": rel, "line": 0,
                    "problem": "does not document %s as an entry point" % label,
                    "text": "",
                })
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    offenders = []
    for check in (check_forbidden_paths, check_labels, check_entry_points):
        rc = check(root, offenders)
        if rc:
            return rc

    if args.json:
        print(json.dumps({"violations": offenders}, indent=2))
    else:
        for o in offenders:
            print("DOCS %s:%d  %s  |  %s" % (o["file"], o["line"], o["problem"], o["text"]))
        print("summary: %d docs violation(s)" % len(offenders))
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
