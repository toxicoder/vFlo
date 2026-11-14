# Shared helpers for the //tools/bazel lanes. Sourced, never executed.
#
# Lanes are host-delegating: they forward to npm and pytest rather than
# re-implementing those suites in Starlark. That means they need the real
# source tree (node_modules and backend/.venv live there and are deliberately
# outside the Bazel graph), so they resolve it from a declared marker file
# rather than from the sandbox working directory.

# Echo the repository root, or fail loudly naming the target that diagnoses it.
gate_root() {
  local here marker resolved dir
  here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  for marker in "$here/../../../bazel-unowned.txt" "$here/gate_support.sh"; do
    [ -e "$marker" ] || continue
    resolved="$(readlink -f "$marker" 2>/dev/null)" || continue
    dir="$(dirname "$resolved")"
    while [ "$dir" != "/" ]; do
      if [ -f "$dir/MODULE.bazel" ] && [ -d "$dir/frontend" ]; then
        echo "$dir"
        return 0
      fi
      dir="$(dirname "$dir")"
    done
  done
  echo "error: could not locate the vlo workspace root from the runfiles tree." >&2
  echo "       Run bazelisk test //tools/bazel:doctor for the host report." >&2
  return 1
}

# A lane may skip only when its toolchain is genuinely absent, and never in CI.
gate_require_or_skip() {
  local what="$1" probe="$2"
  if eval "$probe" >/dev/null 2>&1; then
    return 0
  fi
  if [ "${CI:-}" = "true" ] || [ "${REQUIRE_LINT_TOOLS:-0}" = "1" ]; then
    echo "error: $what is missing but required (CI=true or REQUIRE_LINT_TOOLS=1)." >&2
    echo "       Install it with: bazelisk run //tools/bazel:setup_host" >&2
    exit 1
  fi
  echo "SKIP: $what is not installed; run 'bazelisk run //tools/bazel:setup_host'."
  exit 0
}

# The interpreter the backend suite is pinned to.
gate_backend_python() {
  echo "$1/backend/.venv/bin/python"
}
