# Contributing

Thanks for your interest in contributing to **vlo**.

## Before You Open a Pull Request

- Read the [Individual CLA](./CLA-INDIVIDUAL.md).
- By submitting a contribution to this repository, you agree to the terms of
  the Individual Contributor License Agreement.
- If your contribution includes work you do not fully own, disclose the
  original copyright holder, the applicable open-source license, and a link to
  the original source code in the Pull Request description and in the header
  comments of the contributed file(s).

## Development Notes

- Do not add any big features without getting in touch first; you might end up spending time on something I'm already working on.
- Run the relevant tests before submitting changes.
- Keep contributions focused and include enough context in the Pull Request for
  review.
- If your change affects behavior, documentation updates are appreciated.

## Running the Checks

The repository's checks are Bazel targets, so there is one command and no
script paths to remember:

```bash
bazelisk test //:gate --test_tag_filters= --test_output=errors
```

`//:gate` is the suite CI runs: frontend lint, tests and coverage, the backend
suite and its coverage, the gate's own tests, and the repo-hygiene lanes that
check file ownership, the BUILD-file glob budget, and that the documentation
names targets.

While you iterate, use the fast loop, which needs no host toolchain:

```bash
bazelisk test //:fast --test_tag_filters= --test_output=errors
```

The frontend and backend lanes are host-delegating: they forward to the npm and
venv toolchains that already own those suites. Bazel does not compile the app,
so the npm scripts (`npm run lint`, `npm run test`, `npm run test:backend`)
still work and are the way to run a single suite by hand. Set the host up once
with `bazelisk run //tools/bazel:setup_host` (add `-- --check` to see what is
missing without installing it), and use
`bazelisk test //tools/bazel:doctor --test_tag_filters= --test_output=all` to
see which lanes can run on your machine. A lane whose toolchain is missing skips
locally and fails under `CI=true`.

Adding or deleting a file means regenerating the BUILD files with
`bazelisk run //tools/bazel:build_files -- --write`. Do not hand-edit a
generated BUILD file: `//tools/bazel:build_files_check` compares it against a
fresh render. See [tools/bazel/README.md](tools/bazel/README.md) for the rest of
the gate.
