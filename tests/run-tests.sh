#!/usr/bin/env bash
# run-tests.sh: the local test suite. Run it ON LINUX (the installer refuses other platforms).
#   tests/run-tests.sh            shellcheck (when installed), py_compile, then the unit tests
#   tests/run-tests.sh -k pattern extra arguments go to `python -m unittest` (for example -k uninstall)
# Nothing here uses the network or touches your real home directory: every test builds its own HOME and TMPDIR.
set -Eeuo pipefail
root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd -- "$root"

if [ "$(uname -s)" != Linux ]; then
  echo "run-tests.sh: run these tests on Linux (found $(uname -s))" >&2
  exit 3
fi

mapfile -t shell_files < <(find . -path ./.stack -prune -o -type f \( -name '*.sh' -o -path './bin/*' \) -print | sort)

echo "== shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x "${shell_files[@]}"
  echo "shellcheck: ${#shell_files[@]} files clean"
else
  echo "shellcheck not installed: skipped"
fi

echo "== bash -n"
for f in "${shell_files[@]}"; do bash -n "$f"; done
echo "syntax ok"

if command -v uv >/dev/null 2>&1; then
  py=(uv run --no-project --python 3.13 python)
else
  py=(python3)
fi

echo "== py_compile"
"${py[@]}" -m py_compile lib/*.py tests/*.py
echo "py_compile ok"

echo "== unittest"
"${py[@]}" -m unittest discover -s tests -p 'test_*.py' -v "$@"
