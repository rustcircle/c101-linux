# shellcheck shell=bash
# shellcheck disable=SC2034  # globals defined here are consumed by the entry points and the other lib files
# common.sh: logging, the dry-run wrapper, privilege handling, Python helper dispatch and the state file.
# Sourced by install-linux.sh, uninstall-linux.sh and bin/c101-linux-doctor. Requires C101L_ROOT.

: "${C101L_ROOT:?common.sh needs C101L_ROOT}"

DRY_RUN=${DRY_RUN:-0}
ASSUME_YES=${ASSUME_YES:-0}
NO_SUDO=${NO_SUDO:-0}
PRIV_MANUAL=()          # privileged commands that were printed instead of run
NEXT_STEPS=()           # lines for the closing "next steps" block
C101L_PY_CMD=()         # resolved helper interpreter

log() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
err() { printf 'ERROR: %s\n' "$*" >&2; }
die() {
  err "$*"
  exit 1
}
usage_error() {
  err "$*"
  err "try --help"
  exit 2
}

utc_stamp() { date -u +%Y%m%dT%H%M%SZ; }

# The uid the root checks use. C101_LINUX_FAKE_EUID is a test hook so the refusal can be tested as a normal user.
effective_uid() { printf '%s\n' "${C101_LINUX_FAKE_EUID:-$EUID}"; }

# run CMD...: execute, or print it under --dry-run. Every side effect of the overlay goes through run/run_priv
# or a helper that receives --dry-run.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

# run_priv CMD...: run as root. Root runs it directly; others use sudo (sudo -n without a TTY). With --no-sudo, or
# when sudo is missing, the command is recorded and printed at the end instead, and the call returns 0 with
# PRIV_DEFERRED=1 so callers re-check real state instead of trusting the return code.
PRIV_DEFERRED=0
run_priv() {
  PRIV_DEFERRED=0
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run: (as root)'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  if [ "$(effective_uid)" -eq 0 ]; then
    "$@"
    return
  fi
  if [ "$NO_SUDO" = 1 ] || ! command -v sudo >/dev/null 2>&1; then
    PRIV_DEFERRED=1
    PRIV_MANUAL+=("$(printf '%q ' "$@")")
    if [ "$NO_SUDO" = 1 ]; then
      warn "needs root, not run (--no-sudo): $*"
    else
      warn "needs root, not run (sudo is not installed): $*"
    fi
    return 0
  fi
  if [ -t 0 ]; then
    sudo "$@"
  else
    sudo -n "$@" || {
      err "sudo needs a password and there is no terminal; re-run interactively or use --no-sudo"
      return 1
    }
  fi
}

confirm() { # confirm "question": yes under --yes; otherwise ask on a TTY; no TTY and no --yes means no
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || return 1
  local reply
  read -r -p "$1 [y/N] " reply
  case $reply in y | Y | yes | YES) return 0 ;; *) return 1 ;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }

# Version compare: version_ge A B is true when A >= B (dotted numerics).
version_ge() {
  local a=$1 b=$2 i x y
  local -a av bv
  IFS=. read -r -a av <<<"$a"
  IFS=. read -r -a bv <<<"$b"
  for i in 0 1 2 3; do
    x=${av[i]:-0}
    y=${bv[i]:-0}
    x=${x//[!0-9]/}
    y=${y//[!0-9]/}
    [ "${x:-0}" -gt "${y:-0}" ] && return 0
    [ "${x:-0}" -lt "${y:-0}" ] && return 1
  done
  return 0
}

# --- Python helpers ------------------------------------------------------------------------------------------
# Helpers are stdlib-only and 3.9-compatible. Preference: $C101L_PY (tests), uv's managed 3.13, then python3.
# _probe_helper_python: set C101L_PY_CMD from what exists already; never downloads, never dies.
_probe_helper_python() {
  if [ "${#C101L_PY_CMD[@]}" -gt 0 ]; then return 0; fi
  if [ -n "${C101L_PY:-}" ]; then
    C101L_PY_CMD=("$C101L_PY")
    return 0
  fi
  local p c
  if have uv && p=$(uv python find --no-python-downloads 3.13 2>/dev/null) && [ -n "$p" ]; then
    C101L_PY_CMD=("$p")
    return 0
  fi
  for c in python3 python; do
    if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      C101L_PY_CMD=("$(command -v "$c")")
      return 0
    fi
  done
  return 1
}

# helper_python_available: is there an interpreter for the helpers right now (no download)?
helper_python_available() { _probe_helper_python; }

# dry_plan_only: a dry run on a host with no Python yet shows the plan from bash alone (the helpers need Python).
dry_plan_only() { [ "$DRY_RUN" = 1 ] && ! helper_python_available; }

find_helper_python() {
  if _probe_helper_python; then return 0; fi
  local p
  # Minimal images have no python3: fetch uv's managed 3.13 (c101 needs it anyway), except in a dry run.
  if [ "$DRY_RUN" != 1 ] && have uv && uv python install 3.13 >&2 && p=$(uv python find --no-python-downloads 3.13 2>/dev/null) && [ -n "$p" ]; then
    C101L_PY_CMD=("$p")
    return 0
  fi
  die "no Python for the helpers: install uv (this installer does it) or python3 >= 3.9"
}

# helper NAME ARGS...: run lib/NAME in isolated mode.
helper() {
  local name=$1
  shift
  find_helper_python
  "${C101L_PY_CMD[@]}" -I "$C101L_ROOT/lib/$name" "$@"
}

# --- state ---------------------------------------------------------------------------------------------------
state_file() { printf '%s\n' "${C101L_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/c101-linux/state.json}"; }
state_set() { # state_set KEY JSON
  [ "$DRY_RUN" = 1 ] && return 0
  helper state.py --file "$(state_file)" set "$1" "$2"
}
state_append() { # state_append KEY JSON
  [ "$DRY_RUN" = 1 ] && return 0
  helper state.py --file "$(state_file)" append "$1" "$2"
}
state_get() { # state_get KEY: prints the value, exit 1 when absent
  [ -f "$(state_file)" ] || return 1
  helper state.py --file "$(state_file)" get "$1"
}
state_del() {
  [ "$DRY_RUN" = 1 ] && return 0
  helper state.py --file "$(state_file)" del "$1"
}
json_str() { # json_str STRING: a JSON string literal (for state_set); pure bash so a dry run needs no Python
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  s=${s//$'\r'/\\r}
  printf '"%s"' "$s"
}

# Expand a leading $HOME to a literal ~ so state and output stay portable when printed.
# shellcheck disable=SC2088  # the literal ~ is the point: it is for display only
tilde() { local p=$1; if [ "${p#"$HOME"/}" != "$p" ]; then printf '~/%s\n' "${p#"$HOME"/}"; else printf '%s\n' "$p"; fi; }

next_step() { NEXT_STEPS+=("$*"); }

# Files that deferred root commands refer to live here, so they still exist after this run (the temp dir is gone).
pending_dir() { printf '%s\n' "$(dirname -- "$(state_file)")/pending"; }

# stage_file SRC NAME: copy SRC into the pending dir and print the persistent path (the source path is returned in a dry run).
stage_file() {
  local dir dest
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s\n' "$1"
    return 0
  fi
  dir=$(pending_dir)
  mkdir -p -- "$dir"
  chmod 700 -- "$dir" 2>/dev/null || true
  dest=$dir/$2
  cp -f -- "$1" "$dest"
  printf '%s\n' "$dest"
}

print_manual_and_next() {
  if [ "${#PRIV_MANUAL[@]}" -gt 0 ]; then
    local c script
    printf '\nRun these as root (they were skipped):\n'
    for c in "${PRIV_MANUAL[@]}"; do printf '  sudo %s\n' "$c"; done
    if [ "$DRY_RUN" != 1 ]; then
      script=$(pending_dir)/pending-root-commands.sh
      mkdir -p -- "$(dirname -- "$script")"
      {
        printf '#!/bin/sh\n# Written by c101-linux %s. Review, then run as root: sudo sh %s\nset -eu\n' "$(utc_stamp)" "$script"
        for c in "${PRIV_MANUAL[@]}"; do printf '%s\n' "$c"; done
      } >"$script"
      chmod 600 -- "$script"
      printf 'The same commands, for review, are in %s\n  (run: sudo sh %s)\n' "$script" "$script"
    fi
  fi
  if [ "${#NEXT_STEPS[@]}" -gt 0 ]; then
    printf '\nNext steps:\n'
    local n
    for n in "${NEXT_STEPS[@]}"; do printf '  - %s\n' "$n"; done
  fi
}

# json_field JSON KEY: print one top-level field of a one-line JSON object (booleans as true/false).
json_field() {
  find_helper_python
  "${C101L_PY_CMD[@]}" -I -c '
import json, sys
v = json.loads(sys.argv[1]).get(sys.argv[2])
print("" if v is None else (str(v).lower() if isinstance(v, bool) else (json.dumps(v) if isinstance(v, (list, dict)) else v)))
' "$1" "$2"
}

# tmp_file PREFIX: a fresh file in the run's temp dir (C101L_TMP, created by the entry point) or $TMPDIR.
tmp_file() { mktemp "${C101L_TMP:-${TMPDIR:-/tmp}}/${1:-c101l}.XXXXXX"; }
