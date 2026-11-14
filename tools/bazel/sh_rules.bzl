"""Host-delegating shell rules.

Bazel 9 dropped the native `sh_test` and `sh_binary`. This repo's gate is
deliberately free of registry dependencies: the lanes under //tools/bazel are
plain bash scripts, and the frontend/backend lanes forward to the npm and venv
toolchains that already own those suites (see tools/bazel/README.md).

An executable rule must create its own executable artifact, so each lane gets a
generated wrapper that execs the real script. Bazel stages runfiles as symlinks
back into the checkout under `<executable>.runfiles/_main/`, and it may exec the
test from inside that tree or from the execroot, so the wrapper tolerates both.
Resolving the lane script to a real checkout path is what lets a lane reach
`git ls-files`, frontend/node_modules and backend/.venv -- all three deliberately
live outside the Bazel graph.

`local`, `size`, `tags` and `timeout` arrive as implicit attributes of every
rule; `data` does not, so it is declared here.
"""

def _lane_wrapper(ctx, src):
    """Write the executable this rule owns; it forwards to the lane script."""
    wrapper = ctx.actions.declare_file(ctx.label.name + ".wrapper.sh")
    body = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'self="${BASH_SOURCE[0]}"',
        'here="$(cd "$(dirname "$self")" && pwd)"',
        'src="%s"' % src.path,
        'target=""',
        # try sets $target when the candidate is a readable file.
        'try() { if [ -n "${1:-}" ] && [ -f "$1" ]; then target="$1"; return 0; fi; return 1; }',
        # 1. The pointers Bazel exports itself.
        'try "${RUNFILES_DIR:-}/_main/$src" || try "${RUNFILES_DIR:-}/$src" || true',
        # 2. A materialised runfiles tree, beside the executable or above the cwd.
        'if [ -z "$target" ]; then',
        '  dir="$here"',
        '  while [ "$dir" != "/" ]; do',
        '    if try "$dir/$src"; then break; fi',
        '    for root in "$dir"/*.runfiles; do',
        '      [ -d "$root" ] || continue',
        '      if try "$root/_main/$src" || try "$root/$src"; then break 2; fi',
        '    done',
        '    dir="$(dirname "$dir")"',
        '  done',
        "fi",
        # 3. The manifest, for runs that only got a manifest.
        'if [ -z "$target" ]; then',
        '  for manifest in "${RUNFILES_MANIFEST_FILE:-}" \\',
        '                "$here/$(basename "$self").runfiles_manifest"; do',
        '    [ -n "$manifest" ] && [ -f "$manifest" ] || continue',
        '    found="$(awk -v s="$src" \'$1 ~ ("(^|/)" s "$") {print $2; exit}\' "$manifest")"',
        '    if [ -n "$found" ] && [ -f "$found" ]; then target="$found"; break; fi',
        "  done",
        "fi",
        'if [ -z "$target" ]; then',
        '  echo "error: lane script $src was not staged into runfiles" >&2',
        "  exit 1",
        "fi",
        'exec bash "$target" "$@"',
    ]
    ctx.actions.write(output = wrapper, content = "\n".join(body) + "\n", is_executable = True)
    return wrapper

def _runfiles_impl(ctx):
    src = ctx.file.src
    wrapper = _lane_wrapper(ctx, src)
    runfiles = ctx.runfiles(files = ctx.files.data + [src])
    return [DefaultInfo(executable = wrapper, runfiles = runfiles)]

_SH_ATTRS = {
    "src": attr.label(
        allow_single_file = True,
        mandatory = True,
        doc = "The bash script the lane runs.",
    ),
    "data": attr.label_list(
        allow_files = True,
        doc = "Files the script reads at run time, staged into its runfiles.",
    ),
}

sh_test = rule(
    implementation = _runfiles_impl,
    test = True,
    attrs = _SH_ATTRS,
    doc = "Runs a bash script as a Bazel test.",
)

sh_script = rule(
    implementation = _runfiles_impl,
    executable = True,
    attrs = _SH_ATTRS,
    doc = "Runs a bash script with `bazel run`.",
)
