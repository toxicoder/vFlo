# The Bazel gate

The gate is this repository's definition of done, expressed as Bazel targets.
Every check that decides whether a change passes is something you can name with
a label, so a human never needs a script path. If a doc, a comment, or an error
string tells you to run a file under `tools/bazel/`, that is a defect, and
`//tools/bazel:docs_name_targets` is the lane that keeps it one.

## The one command

```bash
bazelisk test //:gate --test_tag_filters= --test_output=errors
```

`//:gate` is a test suite holding every lane, so that single command is what CI
runs. `--test_tag_filters=` empties the tag filter so no lane in the suite is
quietly dropped, whatever a local configuration sets, and `--test_output=errors`
prints the output of the lanes that fail.

## The fast loop

```bash
bazelisk test //:fast --test_tag_filters= --test_output=errors
```

`//:fast` is the part of the gate that needs no host toolchain: ownership, the
glob budget, BUILD-file drift, and the docs rule. Run it while you iterate, and
`//:gate` before you push.

## The lanes

| Target | Asserts | Needs |
| --- | --- | --- |
| `//tools/bazel:owned_files_check` | every tracked file is target-owned or ledger-excluded | git, bazelisk |
| `//tools/bazel:glob_budget` | no unannotated `glob()`, and never more than the budget allows | git |
| `//tools/bazel:build_files_check` | the checked-in BUILD files match a fresh render | git |
| `//tools/bazel:docs_name_targets` | docs name targets, and every cited label resolves | git, bazelisk |
| `//tools/bazel:doctor` | host report; which lanes can run and which are missing a toolchain | reports only |
| `//tools/bazel:frontend_lint` | eslint over the frontend | `frontend/node_modules` |
| `//tools/bazel:frontend_test` | the vitest suite | `frontend/node_modules` |
| `//tools/bazel:frontend_coverage` | the frontend coverage floor | `frontend/node_modules` |
| `//tools/bazel:backend_test` | the pytest suite, torch lane deselected | `backend/.venv` |
| `//tools/bazel:backend_coverage` | the same suite under `pytest --cov` | `backend/.venv` with pytest-cov |
| `//tools/bazel:gate_tests` | the gate's own Python, held to 100% coverage | pytest and pytest-cov |

Run one lane on its own with the same shape, for example
`bazelisk test //tools/bazel:frontend_lint --test_tag_filters= --test_output=errors`.

## Adding a file

BUILD files are generated, so do not hand-edit one. After adding or deleting a
tracked file, regenerate them:

```bash
bazelisk run //tools/bazel:build_files -- --write
```

`//tools/bazel:build_files_check` compares the checked-in text against a fresh
render, so a BUILD file that drifts from `git ls-files` fails the gate. The
generator writes one BUILD file per directory that directly holds tracked files,
and names each file explicitly in a filegroup. Two packages are hand-authored
and it leaves them alone: the root package, which declares `//:gate` and
`//:fast`, and `//tools/bazel`, which declares every lane.

## The ledger

A tracked file that no target should own goes in `bazel-unowned.txt` at the
repository root, one entry per line, as
`<path-or-glob><two spaces or a tab><reason>`. Two rules, both enforced by
`//tools/bazel:owned_files_check`:

- Every entry needs a reason. An entry with no reason fails.
- A stale entry fails. The lane runs the checker with `--strict-ledger`, so an
  entry that matches no tracked file (a deletion, a typo, a path that has since
  gained an owner) is reported rather than left behind.

Bazel's own control plane (.bazelrc, .bazelversion, MODULE.bazel, and every
BUILD file) is exempt in code and needs no entry.

## The glob budget

A `glob()` silently adopts a new file, which is why the generator writes
explicit lists. Some globs are the contract, though, where the tree itself is
what is shipped. So the rule is not "no globs", it is "no unexplained globs, and
never more of them than yesterday":

- Every `glob(` carries a `# glob-policy: <reason>` comment on its own line or
  within the three lines above it.
- The total stays at or under the number recorded in
  `tools/bazel/glob-budget.txt`. That file is a ratchet: it holds the count at
  adoption, so the only way to raise it is to add a glob and defend it in
  review.

`//tools/bazel:glob_budget` checks both.

## Host toolchains, and the CI-strict rule

The frontend and backend lanes are host-delegating: each forwards to the npm
toolchain or the backend venv that already owns that suite. Bazel does not
compile the frontend or the backend here, and no rules_js or rules_python is in
the graph. The npm entry points still work, and the lanes call exactly them, so
`npm run lint`, `npm run test`, and `npm run test:backend` remain valid ways to
run a suite on its own. Each lane runs `local`, because it reads the real
working tree, where `frontend/node_modules` and `backend/.venv` live outside the
Bazel graph on purpose.

A lane whose toolchain is missing skips locally instead of failing:

```bash
bazelisk run //tools/bazel:setup_host              # install what is missing
bazelisk run //tools/bazel:setup_host -- --check   # report only, install nothing
bazelisk test //tools/bazel:doctor --test_tag_filters= --test_output=all
```

`//tools/bazel:setup_host` is the idempotent bootstrap (frontend dependencies,
backend test venv, version floors). `//tools/bazel:doctor` prints the host
report, naming the tools on PATH, whether `frontend/node_modules` and
`backend/.venv` are present, and which lanes would skip; it fails until the
bootstrap has completed.

A skip is a local convenience, not an escape hatch. With `CI=true` or
`REQUIRE_LINT_TOOLS=1` a lane whose toolchain is missing fails instead of
skipping, so set `REQUIRE_LINT_TOOLS=1` locally when you want the CI behaviour
before you push. The CI job bootstraps first with
`bazelisk run //tools/bazel:setup_host` and then runs `//:gate`, so a host
setup that breaks shows up there rather than on a fresh machine.

## Why sh_rules.bzl exists

Bazel 9 dropped the native `sh_test` and `sh_binary` rules, and this gate stays
registry-free on purpose: `MODULE.bazel` declares no dependencies, so the rules
cannot come from an external ruleset either. `sh_rules.bzl` therefore defines
the two rules the gate needs, `sh_test` and `sh_script`, in plain Starlark.

An executable rule has to create its own executable artifact, so each rule
writes a small wrapper that locates the real lane script and execs it. Bazel
stages runfiles as symlinks back into the checkout under
`<executable>.runfiles/_main/`, and it may run a test from inside that tree or
from the execroot, so the wrapper accepts both. Resolving the lane script to a
real checkout path is what lets a lane reach `git ls-files`,
`frontend/node_modules`, and `backend/.venv`, all three of which deliberately
live outside the Bazel graph.

## What the docs lane holds you to

`//tools/bazel:docs_name_targets` reads every tracked markdown file plus the
lane text a person sees when a lane fails, and reports a violation when a doc
tells someone to run a checked-in gate script by path instead of naming the
target, when a cited label does not resolve in `bazelisk query //...`, or when
one of the entry-point docs (README.md, CONTRIBUTING.md, AGENTS.md) stops naming
`//:gate` or `//:fast`.
