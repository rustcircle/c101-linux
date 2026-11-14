# shellcheck shell=bash
# shellcheck disable=SC2034  # KEYS_* results are read by install-linux.sh (I1), the doctor and the tests
# keys.sh: the installer's key step. It seeds c101's stack.env (${CLAUDE_STACK_ENV:-$HOME/.claude/stack.env}) with
# API keys and the fal image backend, and it touches that file only through lib/stackenv.py (all file I/O is there).
# At runtime c101's servers take their keys from that file and nowhere else: bin/mcp-run (stdio: image, fetch) and
# bin/mcp-headers (HTTP headers: exa, jina, spider, context7, fal). The installer only seeds it (D6).
# Sourced after lib/common.sh (C101L_ROOT, DRY_RUN, ASSUME_YES, step, info, ok, warn, usage_error, have, helper,
# helper_python_available, find_helper_python, json_field, state_get, tilde, effective_uid). Bash >= 4.2; safe under
# set -Eeuo pipefail.
#
# Key order, used for imports, prompts and messages alike (D3, D5):
#   FAL_KEY, EXA_API_KEY, JINA_API_KEY, SPIDER_API_KEY, CONTEXT7_API_KEY (optional),
#   then the extras FIRECRAWL_API_KEY, TAVILY_API_KEY, BRAVE_API_KEY, REPLICATE_API_TOKEN, RUNWARE_API_KEY,
#   then any IMAGE_<OP>_{URL,KEY,MODEL,API,PARAMS} exported in the environment (another image provider, no preset).
# Extras are for optional servers (--mcp firecrawl, tavily, brave-search, replicate, runware). D3/D5 put them last in
# the interactive step, so they are asked too: directly when their server is in --mcp, otherwise only after a single
# y/N question that defaults to No, so the usual run asks at most five questions.
#
# Secret handling:
#   - values come only from environment variables with exactly those names or from a hidden `read -r -s` prompt;
#   - they reach `stackenv.py merge` only on its stdin, written by the printf builtin into a process substitution:
#     never in argv, never in a file, never traced (xtrace is off while the step runs; this file never enables it
#     while a value is held);
#   - only NAMES are printed or logged; a dry run prints `would set NAME` lines, never values;
#   - a value stackenv.py would refuse (C0/C1 control, DEL, U+2028, U+2029, invalid UTF-8, too long) is dropped here by
#     name, with the same rules on both sides (_keys_value_ok), so one bad value never fails the whole merge;
#   - while the hidden prompts run, Ctrl-C or TERM restores the terminal (echo) and the caller's INT/TERM traps, drops
#     the held values and re-sends the signal (bash 4.2/4.3 may leave echo off after `read -s`);
#   - when the step ends the values are dropped and every key variable (the names above and IMAGE_<OP>_*) is unset in
#     the installer process, so later children (c101's installer, npx/uvx pre-warm, podman) never inherit them (P7).
# File safety (paths and modes only): stackenv.py check runs first. A stack.env of yours that group or others can read
# is tightened to 600 by the merge, also on a run that changes nothing else (reported by path); one owned by another
# account is not touched (warning, no prompt); a directory another account can write (the target's, or the link's) is
# warned about by path. Backups (<target>.bak-<UTC>, mode 600) are never deleted: the step prints the new one's path and
# how many sit next to the file. On the paths that leave the file alone (key step off, no Python) it only warns.
#
# ---- Contract for install-linux.sh (node I1) ---------------------------------------------------------------------
# Source this file with the other libs at the top, BEFORE parse_args: sourcing resets the three inputs below, so a
# value inherited from the caller's environment never counts (D2: KEYS_OVERWRITE comes from --keys-overwrite only).
# Before the FIRST child process (long before keys_step), the caller must `export -n` every key name above and every
# IMAGE_<OP>_* (install-linux.sh key_env_unexport, P7): otherwise c101's installer and anything started before the key
# step inherit the keys. keys_step / keys_scrub_env only unset them afterwards. Any new entry point must do the same.
# Inputs (globals that I1 assigns after parse_args and apply_overrides):
#   KEYS            0 | env | prompt | auto. Profile key KEYS; --keys sets prompt, --keys-from-env sets env.
#                   Empty: $P_KEYS, else 0.
#                   0       nothing is imported or asked (IMAGE_BACKEND=fal still merges the preset, key from the file)
#                   env     import the named non-empty environment variables; no prompt is reachable (ci, headless)
#                   prompt  env import, then a hidden prompt for each key that is still missing (every key, showing
#                           (set) or (empty), with --keys-overwrite). Asked on a terminal even under --yes, because the
#                           flag asked for it. No terminal: one warning, no prompt.
#                   auto    env import, then the prompts only when interactive (stdin is a terminal and ASSUME_YES != 1)
#   IMAGE_BACKEND   fal | none | keep. Profile key IMAGE_BACKEND, --image-backend. Empty: $P_IMAGE_BACKEND, else keep.
#                   Anything else is a usage_error (exit 2).
#                   fal   merges $C101_SRC/config/stack.env.fal.example (--preset, --placeholder your-fal-key,
#                         --preset-key FAL_KEY): one fal key fills FAL_KEY and the KEY of every fal operation. With no
#                         fal key from the environment, the file or the prompt, the URL/MODEL lines are still written
#                         with EMPTY keys, one warning names FAL_KEY and prints the exact re-run command
#                         `install-linux.sh --keys --keys-only` (D3/D13/D14; ci and --yes runs: the same warning also
#                         gives the unattended form, FAL_KEY exported, then `install-linux.sh --keys-only --keys-from-env`).
#                         Operations already on another provider are kept whole and reported by operation name (kept_ops).
#                   none, keep  change no image setting.
#                   D12: a recorded none is sticky; install-linux.sh (image_backend_sticky) passes none here unless
#                   --image-backend was given. Every printed re-run command carries a non-fal effective backend
#                   (keys_rerun_cmd, keys_rerun_hint), so no hint turns fal back on.
#   KEYS_OVERWRITE  1 only after --keys-overwrite (never from a profile or any other flag, D2): the only path to
#                   `stackenv.py merge --overwrite`, which takes a mode-600 backup first.
#   Also read: C101_SRC (config/stack.env.example is the template when stack.env is absent; the fal preset),
#   CLAUDE_STACK_ENV, DRY_RUN, ASSUME_YES, OPT_MCP (an extra whose server is in --mcp is asked without the y/N
#   question), PROFILE (added to the re-run hint when it is not standard).
#
# keys_validate     usage_error (exit 2) unless KEYS, IMAGE_BACKEND and KEYS_OVERWRITE are valid; else returns 0.
#                   Call it once the inputs are set, before anything is installed.
# keys_step         the whole step: prints its own `==>` header, validates, imports, prompts, runs ONE merge, reports by
#                   name, then calls keys_scrub_env. Returns 0 in every case except usage_error. A read-only target
#                   (stackenv.py exit 4), a missing Python or another stackenv.py error only warns: keys never stop the
#                   install. Call it after c101_run and BEFORE any pre-warm child.
# keys_only         the --keys-only entry point: call it after detect_all and resolve_source, then exit 0. It touches
#                   stack.env only (no c101 installer, no other overlay step). Returns 0.
# keys_scrub_env    unsets FAL_KEY ... RUNWARE_API_KEY and every IMAGE_<OP>_{URL,KEY,MODEL,API,PARAMS} variable; returns 0.
#                   keys_step calls it; I1 calls it as well on any path that skips keys_step before spawning children.
# Outputs (globals; names and fixed words only, never values; only the backend belongs in the state file):
#   KEYS_BACKEND_EFFECTIVE  fal | none | keep, for state image_backend. With IMAGE_BACKEND=keep it is the value already
#                           recorded in state when that is fal or none (keep never erases a recorded fal), else keep.
#   KEYS_RESULT       ok (merged, or only tightened to mode 600) | nochange (nothing to merge) | dry-run | skipped
#                     (KEYS=0 and backend none/keep, or no Python: with backend fal the warning names FAL_KEY) |
#                     readonly (stackenv.py exit 4) | failed (any other stackenv.py error, or a stack.env owned by
#                     another account, which is never asked for or written)
#   KEYS_FAL_MISSING  1 when IMAGE_BACKEND=fal and no fal key is in the file afterwards (the warning was printed), else 0
#   KEYS_CHANGED, KEYS_KEPT, KEYS_MISSING  space-separated key names; KEYS_KEPT_OPS  space-separated operation names
#   KEYS_STACK_ENV    the stack.env path that was used
# ------------------------------------------------------------------------------------------------------------------

KEYS_PRIMARY=(
  FAL_KEY          # fal: image generation, the main use of c101 (asked first)
  EXA_API_KEY      # research: exa
  JINA_API_KEY     # research: jina
  SPIDER_API_KEY   # research: spider (that server requires a key)
  CONTEXT7_API_KEY # research: context7 (optional)
)
KEYS_EXTRAS=(
  FIRECRAWL_API_KEY   # --mcp firecrawl
  TAVILY_API_KEY      # --mcp tavily
  BRAVE_API_KEY       # --mcp brave-search
  REPLICATE_API_TOKEN # --mcp replicate
  RUNWARE_API_KEY     # --mcp runware
)
KEYS_ORDER=("${KEYS_PRIMARY[@]}" "${KEYS_EXTRAS[@]}")
KEYS_OP_RE='^IMAGE_[A-Z0-9_]+_(URL|KEY|MODEL|API|PARAMS)$'
KEYS_FAL_PLACEHOLDER=your-fal-key
KEYS_MAX_VALUE=16384 # stackenv.py MAX_VALUE_CHARS

# Inputs: reset when sourced (see the contract above).
KEYS=""
IMAGE_BACKEND=""
KEYS_OVERWRITE=0

# Outputs.
KEYS_BACKEND_EFFECTIVE=""
KEYS_RESULT=""
KEYS_FAL_MISSING=0
KEYS_CHANGED=""
KEYS_KEPT=""
KEYS_KEPT_OPS=""
KEYS_MISSING=""
KEYS_STACK_ENV=""

# Internal state. _KEYS_VAL holds values only while keys_step runs; everything else holds names.
declare -gA _KEYS_VAL=()   # NAME -> value
_KEYS_NAMES=()             # names held in _KEYS_VAL, in key order
declare -gA _KEYS_PRE=()   # NAME -> set|empty|absent in the file before the merge
_KEYS_WOULD_ASK=""         # dry run: names a real run would ask for
_KEYS_FAL_WARNED=0
_KEYS_ONLY=0               # 1 while keys_only runs
_KEYS_FIX_MODE=0           # 1 when check found a stack.env of yours that group/others can access (the merge tightens it)
_KEYS_CHECK_MODE=""        # its permission bits as check reported them (e.g. 644)
_KEYS_TTY=""               # `stty -g` of the terminal while the hidden prompts run
_KEYS_PREV_TRAPS=""        # the caller's INT and TERM traps (trap -p), put back after the prompts

# What stackenv.py accepts in a value, as bytes: valid UTF-8 without C0 controls, DEL, C1 controls (U+0080-U+009F, so
# U+0085), U+2028 or U+2029. Same set as its _BAD_VALUE_CHARS plus its UTF-8 decoding; matched under LC_ALL=C.
_KEYS_VALUE_RE=$'^([\x20-\x7e]|\xc2[\xa0-\xbf]|[\xc3-\xdf][\x80-\xbf]|\xe0[\xa0-\xbf][\x80-\xbf]|\xe2\x80[\x80-\xa7\xaa-\xbf]|\xe2[\x81-\xbf][\x80-\xbf]|[\xe1\xe3-\xec\xee\xef][\x80-\xbf][\x80-\xbf]|\xed[\x80-\x9f][\x80-\xbf]|\xf0[\x90-\xbf][\x80-\xbf][\x80-\xbf]|[\xf1-\xf3][\x80-\xbf][\x80-\xbf][\x80-\xbf]|\xf4[\x80-\x8f][\x80-\xbf][\x80-\xbf])*$'

keys_stack_env_path() { printf '%s\n' "${CLAUDE_STACK_ENV:-$HOME/.claude/stack.env}"; }

_keys_mode() { printf '%s\n' "${KEYS:-${P_KEYS:-0}}"; }
_keys_backend() { printf '%s\n' "${IMAGE_BACKEND:-${P_IMAGE_BACKEND:-keep}}"; }

keys_validate() {
  case $(_keys_mode) in
    0 | env | prompt | auto) ;;
    *) usage_error "KEYS must be 0, env, prompt or auto (profile key KEYS; flags --keys, --keys-from-env)" ;;
  esac
  case $(_keys_backend) in
    fal|none|keep) ;;
    *) usage_error "--image-backend must be fal, none or keep" ;;
  esac
  case ${KEYS_OVERWRITE:-0} in
    0 | 1) ;;
    *) usage_error "KEYS_OVERWRITE must be 0 or 1 (set by --keys-overwrite only)" ;;
  esac
  return 0
}

# ---- small helpers (names only) ----------------------------------------------------------------------------------

# _keys_listed NAME "LIST": is NAME a word of the space-separated LIST?
_keys_listed() {
  case " $2 " in *" $1 "*) return 0 ;; esac
  return 1
}

# _keys_json_list JSON FIELD: the names in a one-line JSON list field of stackenv.py's output, space-separated.
# The lists hold names only (^[A-Z0-9_]+$); anything else is dropped.
_keys_json_list() {
  local re="\"$2\":[[:space:]]*\\[([^]]*)\\]" items n out=""
  if [[ $1 =~ $re ]]; then
    items=${BASH_REMATCH[1]//\"/}
    items=${items//,/ }
    for n in $items; do
      if [[ $n =~ ^[A-Z0-9_]+$ ]]; then out+=" $n"; fi
    done
  fi
  printf '%s\n' "${out# }"
}

# _keys_json_word JSON FIELD: a lower-case code field such as "reason" (empty when absent or null).
_keys_json_word() {
  local re="\"$2\":[[:space:]]*\"([a-z0-9-]+)\""
  if [[ $1 =~ $re ]]; then printf '%s\n' "${BASH_REMATCH[1]}"; else printf '\n'; fi
}

# _keys_json_true JSON FIELD: is the boolean field true?
_keys_json_true() {
  local re="\"$2\":[[:space:]]*true"
  [[ $1 =~ $re ]]
}

# _keys_json_false JSON FIELD: is the boolean field false (not null, not absent)?
_keys_json_false() {
  local re="\"$2\":[[:space:]]*false"
  [[ $1 =~ $re ]]
}

# _keys_json_int JSON FIELD: a non-negative integer field (empty when absent).
_keys_json_int() {
  local re="\"$2\":[[:space:]]*([0-9]+)"
  if [[ $1 =~ $re ]]; then printf '%s\n' "${BASH_REMATCH[1]}"; else printf '\n'; fi
}

# _keys_json_codes JSON FIELD: the lower-case codes in a one-line JSON list field (e.g. dir_issues), space-separated.
_keys_json_codes() {
  local re="\"$2\":[[:space:]]*\\[([^]]*)\\]" items n out=""
  if [[ $1 =~ $re ]]; then
    items=${BASH_REMATCH[1]//\"/}
    items=${items//,/ }
    for n in $items; do
      if [[ $n =~ ^[a-z0-9-]+$ ]]; then out+=" $n"; fi
    done
  fi
  printf '%s\n' "${out# }"
}

# _keys_fold "NAMES": the same list with IMAGE_<OP>_<FIELD> folded to IMAGE_<OP>_* and CONTEXT7 marked optional.
_keys_fold() {
  local n out="" seen=" "
  for n in $1; do
    if [[ $n =~ ^(IMAGE_[A-Z0-9_]+)_(URL|KEY|MODEL|API|PARAMS)$ ]]; then n="${BASH_REMATCH[1]}_*"; fi
    if [ "$n" = CONTEXT7_API_KEY ]; then n="CONTEXT7_API_KEY(optional)"; fi
    case $seen in *" $n "*) continue ;; esac
    seen+="$n "
    out+=" $n"
  done
  printf '%s\n' "${out# }"
}

_keys_extra_server() {
  case $1 in
    FIRECRAWL_API_KEY) printf 'firecrawl\n' ;;
    TAVILY_API_KEY) printf 'tavily\n' ;;
    BRAVE_API_KEY) printf 'brave-search\n' ;;
    REPLICATE_API_TOKEN) printf 'replicate\n' ;;
    RUNWARE_API_KEY) printf 'runware\n' ;;
    *) printf '\n' ;;
  esac
}

_keys_label() {
  case $1 in
    FAL_KEY)
      # D12: with another effective backend the key serves only the hosted fal MCP (fal-runner); it never turns the
      # image backend back on, so the prompt does not promise image generation.
      if [ "${KEYS_BACKEND_EFFECTIVE:-fal}" = fal ]; then
        printf '%s\n' "fal.ai image generation, the main use (fal.ai/dashboard/keys)"
      else
        printf '%s\n' "fal.ai, used by the fal-runner agent only: the image backend stays ${KEYS_BACKEND_EFFECTIVE} (fal.ai/dashboard/keys)"
      fi
      ;;
    EXA_API_KEY) printf '%s\n' "exa research search" ;;
    JINA_API_KEY) printf '%s\n' "jina reader and search" ;;
    SPIDER_API_KEY) printf '%s\n' "spider crawler (that server needs a key)" ;;
    CONTEXT7_API_KEY) printf '%s\n' "context7 library docs (optional)" ;;
    *) printf 'optional %s server (--mcp %s)\n' "$(_keys_extra_server "$1")" "$(_keys_extra_server "$1")" ;;
  esac
}

# _keys_extra_requested NAME: is the extra's server in --mcp?
_keys_extra_requested() {
  local s
  s=$(_keys_extra_server "$1")
  if [ -z "$s" ]; then return 1; fi
  case ",${OPT_MCP:-}," in *",$s,"*) return 0 ;; esac
  return 1
}

# keys_unattended: does a re-run like this one reach no prompt? Profile ci (keys from the environment only) or --yes
# (ASSUME_YES=1). Its hints (keys_rerun_hint) then add the unattended form, the key exported first and
# `--keys-only --keys-from-env`, to the exact terminal command `install-linux.sh --keys --keys-only` (D13, D14), as the
# doctor's ci hint does.
keys_unattended() {
  if [ "${PROFILE:-}" = ci ] || [ "${ASSUME_YES:-0}" = 1 ]; then return 0; fi
  return 1
}

# keys_rerun_cmd [env] [EXTRA]: one re-run command (also used by install-linux.sh). No mode (or `only`, the pre-D14
# spelling, which prints the same): `install-linux.sh --keys --keys-only` (D14), the user's literal D3 command
# `install-linux.sh --keys` (hidden prompt, run from a terminal) limited by --keys-only to the keys and image-backend step,
# so following a hint never re-applies a profile's defaults (FAL_MCP, PRESET, the learning timer, ...) over the choices
# of an earlier install (e.g. `--preset research --no-fal-mcp`). env: `install-linux.sh --keys-only --keys-from-env`
# (the key exported first, never a prompt). --profile follows when it is not standard, and --image-backend with the
# effective backend when that is not fal (D12: a printed hint never turns fal image generation back on behind the user's
# back); nothing else follows except EXTRA (e.g. `--source PATH`).
keys_rerun_cmd() {
  local root sfx=""
  root=$(tilde "$C101L_ROOT")
  sfx+=$(rerun_profile_sfx)
  case ${KEYS_BACKEND_EFFECTIVE:-} in none | keep) sfx+=" --image-backend $KEYS_BACKEND_EFFECTIVE" ;; esac
  if [ -n "${2:-}" ]; then sfx+=" $2"; fi
  case ${1:-} in
    env) printf '%s\n' "$root/install-linux.sh --keys-only --keys-from-env$sfx" ;;
    *) printf '%s\n' "$root/install-linux.sh --keys --keys-only$sfx" ;;
  esac
}

# keys_rerun_hint [MODE] [WHAT] [EXTRA]: the fix every key warning and hint prints (D13, D14). MODE is empty (`only`
# prints the same, see keys_rerun_cmd). Interactive runs: keys_rerun_cmd alone, `install-linux.sh --keys --keys-only`.
# ci and --yes runs cannot prompt, so the hint names that exact command for a re-run from a terminal (hidden prompt)
# AND the unattended form: export WHAT (default: the keys) from a secret, never typed on the command line, then run
# `install-linux.sh --keys-only --keys-from-env`. EXTRA follows both commands. Names only, never a value. Every
# command it prints carries --keys-only (D14).
keys_rerun_hint() {
  local what=${2:-the keys}
  if ! keys_unattended; then
    keys_rerun_cmd "${1:-}" "${3:-}"
    return 0
  fi
  printf '%s\n' "$(keys_rerun_cmd "" "${3:-}") from a terminal (hidden prompt), or unattended: export $what (e.g. from a CI secret; never type a key on the command line), then run $(keys_rerun_cmd env "${3:-}")"
}

# _keys_python: an interpreter for stackenv.py. Outside a dry run uv may fetch its managed 3.13 (as
# find_helper_python does). Never dies.
_keys_python() {
  if helper_python_available; then return 0; fi
  if [ "$DRY_RUN" = 1 ] || ! have uv; then return 1; fi
  (find_helper_python) >/dev/null 2>&1 || return 1
  helper_python_available
}

# _keys_backend_effective BACKEND: set KEYS_BACKEND_EFFECTIVE (keep -> the backend already recorded in state).
_keys_backend_effective() {
  local prev=""
  case $1 in
    fal) KEYS_BACKEND_EFFECTIVE=fal ;;
    none) KEYS_BACKEND_EFFECTIVE=none ;;
    *)
      KEYS_BACKEND_EFFECTIVE=keep
      if helper_python_available; then prev=$(state_get image_backend 2>/dev/null) || prev=""; fi
      case $prev in fal | none) KEYS_BACKEND_EFFECTIVE=$prev ;; esac
      ;;
  esac
  return 0
}

# ---- values: taking, emitting, forgetting --------------------------------------------------------------------------

# _keys_value_ok VALUE: would stackenv.py accept this value's characters? Matched on bytes in a subshell with
# LC_ALL=C (the caller's locale is untouched; the value stays in this process, never in argv), so the answer is the same
# in every locale and equals stackenv.py's rule: valid UTF-8, no C0/C1 control, DEL, U+2028 or U+2029. If the regex
# cannot be compiled here (it then does not even match ""), the [[:cntrl:]] test decides; stackenv.py stays the backstop.
_keys_value_ok() {
  (
    LC_ALL=C
    if [[ $1 =~ $_KEYS_VALUE_RE ]]; then exit 0; fi
    if [[ "" =~ $_KEYS_VALUE_RE ]]; then exit 1; fi
    [[ $1 != *[[:cntrl:]]* ]]
  )
}

# _keys_take NAME VALUE SOURCE: hold a value for the merge. Surrounding whitespace is trimmed; an empty value or the
# fal placeholder is dropped; a value stackenv.py would refuse (control or line-separator character, invalid UTF-8,
# too long) is dropped by name so one bad value cannot block the others.
_keys_take() {
  local name=$1 v=$2
  v=${v#"${v%%[![:space:]]*}"}
  v=${v%"${v##*[![:space:]]}"}
  if [ -z "$v" ] || [ "$v" = "$KEYS_FAL_PLACEHOLDER" ]; then return 0; fi
  if [ "${#v}" -gt "$KEYS_MAX_VALUE" ] || ! _keys_value_ok "$v"; then
    warn "$name ($3): the value is too long or holds a control or line-separator character (or is not UTF-8); it was ignored"
    return 0
  fi
  if [ -z "${_KEYS_VAL[$name]+x}" ]; then _KEYS_NAMES+=("$name"); fi
  _KEYS_VAL[$name]=$v
  return 0
}

# _keys_emit_pairs: NAME=VALUE lines for stackenv.py's stdin. printf is a builtin: nothing reaches argv.
_keys_emit_pairs() {
  local n
  for n in ${_KEYS_NAMES[@]+"${_KEYS_NAMES[@]}"}; do
    printf '%s=%s\n' "$n" "${_KEYS_VAL[$n]-}"
  done
}

_keys_forget() {
  _KEYS_VAL=()
  _KEYS_NAMES=()
}

keys_scrub_env() {
  local n
  for n in "${KEYS_ORDER[@]}"; do unset "$n" 2>/dev/null || true; done
  for n in "${!IMAGE_@}"; do
    if [[ $n =~ $KEYS_OP_RE ]]; then unset "$n" 2>/dev/null || true; fi
  done
  return 0
}

# ---- sources -------------------------------------------------------------------------------------------------------

# _keys_load_status FILE: _KEYS_PRE[NAME] = set|empty|absent for every name in KEYS_ORDER (stackenv.py names; the
# fal placeholder counts as empty). A file it cannot parse leaves everything absent; the merge reports the cause.
_keys_load_status() {
  local file=$1 out="" n re list
  _KEYS_PRE=()
  list=$(IFS=,; printf '%s' "${KEYS_ORDER[*]}")
  out=$(helper stackenv.py names --file "$file" --placeholder "$KEYS_FAL_PLACEHOLDER" --keys "$list" 2>/dev/null) || out=""
  for n in "${KEYS_ORDER[@]}"; do
    re="\"$n\":[[:space:]]*\"(set|empty|absent)\""
    if [[ $out =~ $re ]]; then _KEYS_PRE[$n]=${BASH_REMATCH[1]}; else _KEYS_PRE[$n]=absent; fi
  done
  return 0
}

# _keys_collect_env: import the named variables that are non-empty in the environment, in key order, then any
# IMAGE_<OP>_{URL,KEY,MODEL,API,PARAMS}. Never prompts.
_keys_collect_env() {
  local n v
  for n in "${KEYS_ORDER[@]}"; do
    v=${!n-}
    if [ -n "$v" ]; then _keys_take "$n" "$v" environment; fi
  done
  for n in "${!IMAGE_@}"; do
    if [[ $n =~ $KEYS_OP_RE ]]; then
      v=${!n-}
      if [ -n "$v" ]; then _keys_take "$n" "$v" environment; fi
    fi
  done
  v=""
  if [ "${#_KEYS_NAMES[@]}" -gt 0 ]; then
    info "from the environment: $(_keys_fold "${_KEYS_NAMES[*]}")"
  else
    info "from the environment: none of the key names is set"
  fi
  return 0
}

# _keys_wants_prompt NAME: not taken from the environment, and still missing in the file (any status with
# --keys-overwrite).
_keys_wants_prompt() {
  if [ -n "${_KEYS_VAL[$1]+x}" ]; then return 1; fi
  if [ "${_KEYS_PRE[$1]:-absent}" = set ] && [ "${KEYS_OVERWRITE:-0}" != 1 ]; then return 1; fi
  return 0
}

_keys_status_word() { # set or empty: all a prompt ever shows about a value
  if [ "${_KEYS_PRE[$1]:-absent}" = set ]; then printf 'set\n'; else printf 'empty\n'; fi
}

# _keys_tty_guard: before the hidden prompts, save the terminal mode and the caller's INT/TERM traps, then trap both
# signals so echo comes back even where `read -s` does not restore it itself (bash 4.2/4.3).
_keys_tty_guard() {
  _KEYS_TTY=""
  if have stty; then _KEYS_TTY=$(stty -g 2>/dev/null) || _KEYS_TTY=""; fi
  _KEYS_PREV_TRAPS="$(trap -p INT)"$'\n'"$(trap -p TERM)"
  trap '_keys_tty_signal INT' INT
  trap '_keys_tty_signal TERM' TERM
  return 0
}

# _keys_tty_unguard: the saved terminal mode back (echo on), and the caller's INT/TERM traps back in place.
_keys_tty_unguard() {
  if [ -n "$_KEYS_TTY" ]; then stty "$_KEYS_TTY" 2>/dev/null || stty echo 2>/dev/null || true; fi
  trap - INT TERM
  if [ -n "${_KEYS_PREV_TRAPS//[[:space:]]/}" ]; then eval "$_KEYS_PREV_TRAPS"; fi
  _KEYS_TTY="" _KEYS_PREV_TRAPS=""
  return 0
}

# _keys_tty_signal SIGNAL: the INT/TERM handler while the prompts run. Restores the terminal and the caller's traps,
# drops every held value, then sends the signal again so the caller's own trap (or the default, exit) handles it.
_keys_tty_signal() {
  printf '\n' >&2
  _keys_tty_unguard
  _keys_forget
  kill -s "$1" "$$"
}

# _keys_ask NAME: one hidden prompt. Enter keeps the current value.
_keys_ask() {
  local name=$1 v=""
  IFS= read -r -s -p "    $name - $(_keys_label "$name") ($(_keys_status_word "$name"); Enter keeps it): " v || v=""
  printf '\n' >&2
  _keys_take "$name" "$v" prompt
  v=""
  return 0
}

# _keys_prompt_all: the interactive part. Reached only with KEYS=prompt or KEYS=auto on a terminal (never with 0 or
# env). FAL_KEY first, then the research keys, then the extras.
_keys_prompt_all() {
  local n ans=""
  local -a todo=() gated=()
  for n in "${KEYS_PRIMARY[@]}"; do
    if _keys_wants_prompt "$n"; then todo+=("$n"); fi
  done
  for n in "${KEYS_EXTRAS[@]}"; do
    if ! _keys_wants_prompt "$n"; then continue; fi
    if _keys_extra_requested "$n"; then todo+=("$n"); else gated+=("$n"); fi
  done
  if [ "${#todo[@]}" -eq 0 ] && [ "${#gated[@]}" -eq 0 ]; then
    info "every key is already set: nothing to ask (--keys-overwrite asks for set keys too)"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    if [ "${#todo[@]}" -gt 0 ]; then
      _KEYS_WOULD_ASK="${todo[*]}"
      info "would ask (hidden input) for: $_KEYS_WOULD_ASK"
    fi
    if [ "${#gated[@]}" -gt 0 ]; then info "would offer the optional keys: ${gated[*]}"; fi
    return 0
  fi
  info "API keys for $(tilde "$KEYS_STACK_ENV"): input is hidden, Enter keeps the current value, only names are shown."
  _keys_tty_guard
  for n in ${todo[@]+"${todo[@]}"}; do _keys_ask "$n"; done
  if [ "${#gated[@]}" -gt 0 ]; then
    read -r -p "    Optional keys for servers you have not enabled (${gated[*]}): enter any now? [y/N] " ans || ans=""
    case $ans in
      y | Y | yes | YES) for n in "${gated[@]}"; do _keys_ask "$n"; done ;;
    esac
  fi
  _keys_tty_unguard
  return 0
}

# ---- the merge and its report ----------------------------------------------------------------------------------------

_keys_readonly_help() { # FILE REASON
  local shown reason=${2:-not-writable}
  shown=$(tilde "$1")
  warn "$shown cannot be rewritten ($reason): no key and no image setting was written; the install goes on."
  case $reason in
    nix-store | read-only-mount)
      info "It resolves into a read-only place, for example a home-manager link into /nix/store. Keys must never go"
      info "into the Nix store (it is world-readable). By hand: drop that file from your Nix/home-manager config, remove"
      info "the link ($shown), then re-run the key step, which creates a private regular file (mode 600) from c101's"
      info "template: $(keys_rerun_hint). Or point CLAUDE_STACK_ENV at a private writable"
      info "file (it must also be set where Claude starts)."
      ;;
    *)
      info "You cannot write it or its directory: it should be your own file, mode 600 (check: ls -l $shown)."
      info "Fix that, then re-run $(keys_rerun_hint)"
      ;;
  esac
}

# _keys_check_report JSON CAN_FIX: act on stackenv.py check's owner, mode and directory fields (SEC-3, SEC-10): warn by
# path, never about a value. CAN_FIX=1: a group/other-accessible file of yours is left to the merge, which tightens it
# to 600 (_KEYS_FIX_MODE=1); CAN_FIX=0: a warning with the chmod command only. Returns 1 when the file belongs to
# another account (and we are not root): stackenv.py would refuse it, so nothing should be asked for.
_keys_check_report() {
  local j=$1 can_fix=$2 target shown side d what issues words c
  _KEYS_FIX_MODE=0
  _KEYS_CHECK_MODE=$(_keys_json_word "$j" mode)
  target=$(json_field "$j" target 2>/dev/null) || target=""
  shown=$(tilde "${target:-$KEYS_STACK_ENV}")
  for side in dir link_dir; do
    issues=$(_keys_json_codes "$j" "${side}_issues")
    if [ -z "$issues" ]; then continue; fi
    d=$(json_field "$j" "$side" 2>/dev/null) || d=""
    words=""
    for c in $issues; do
      case $c in
        foreign-owner) words+=", owned by another account" ;;
        group-writable) words+=", group-writable" ;;
        world-writable) words+=", world-writable" ;;
        *) words+=", $c" ;;
      esac
    done
    what="the directory of $shown"
    if [ "$side" = link_dir ]; then what="the directory of the stack.env link"; fi
    warn "$(tilde "${d:-?}") ($what) is ${words#, }: another local account could swap the file, and c101's servers take their keys and environment from it. Keep stack.env in a directory only you can write (fix it with chmod go-w, or move the file, e.g. to ~/.claude)"
  done
  if ! _keys_json_true "$j" exists; then return 0; fi
  if _keys_json_false "$j" owner_ok && [ "$(effective_uid)" != 0 ]; then
    warn "$shown is not owned by you: it is left alone and no key is asked for. A key file must be yours: sudo chown $(id -un) $shown && chmod 600 $shown, or point CLAUDE_STACK_ENV at a file of your own"
    return 1
  fi
  if _keys_json_false "$j" mode_ok; then
    if [ "$can_fix" = 1 ]; then
      _KEYS_FIX_MODE=1
    else
      warn "$shown has mode ${_KEYS_CHECK_MODE:-?}: group or others can read your keys. Fix: chmod 600 $shown"
    fi
  fi
  return 0
}

# _keys_perm_warn_bash FILE: without Python, the owner and mode check from bash alone (test -O, ls -lLd). Warns only.
_keys_perm_warn_bash() {
  local f=$1 shown perm=""
  if [ ! -e "$f" ]; then return 0; fi
  shown=$(tilde "$f")
  if [ ! -O "$f" ]; then
    warn "$shown is not owned by you: a key file must be yours (sudo chown $(id -un) $shown && chmod 600 $shown)"
    return 0
  fi
  perm=$(LC_ALL=C ls -ldL -- "$f" 2>/dev/null) || perm=""
  perm=${perm%%[[:space:]]*}
  if [ "${#perm}" -ge 10 ] && [ "${perm:4:6}" != "------" ]; then
    warn "$shown has mode ${perm:1:9}: group or others can read your keys. Fix: chmod 600 $shown"
  fi
  return 0
}

# _keys_perm_warn FILE: on the paths that leave stack.env alone (key step off, no Python), still warn by path when it
# is readable by others, not yours, or in a directory others can write. Never changes anything.
_keys_perm_warn() {
  local j=""
  if helper_python_available; then
    j=$(helper stackenv.py check --file "$1" 2>/dev/null) || true
    if [ -n "$j" ]; then _keys_check_report "$j" 0 || true; fi
    return 0
  fi
  _keys_perm_warn_bash "$1"
}

_keys_fal_warning() {
  if [ "$_KEYS_FAL_WARNED" = 1 ]; then return 0; fi
  _KEYS_FAL_WARNED=1
  # D3/D13/D14: ONE warning, by name only, with the exact re-run command `install-linux.sh --keys --keys-only`. ci and
  # --yes runs cannot prompt, so the same warning also gives the unattended form (FAL_KEY exported, --keys-only
  # --keys-from-env), as the doctor's ci hint does (keys_rerun_hint).
  warn "FAL_KEY is not set, so fal image generation stays off (its operations are written with empty keys). Add the key: $(keys_rerun_hint "" FAL_KEY)"
  info "(--keys-only limits the re-run to this step, so the choices of your install stay as they are; a key typed on a command line lands in your shell history)"
}

# _keys_report BACKEND USED_PRESET MISSING_KEYS: compute KEYS_MISSING and KEYS_FAL_MISSING, warn once about FAL_KEY,
# print the names-only summary line.
_keys_report() {
  local backend=$1 used=$2 mk=$3 n missing="" fal_missing=0 label=keys
  for n in "${KEYS_PRIMARY[@]}"; do
    if [ "$n" = FAL_KEY ] && [ "$used" = 1 ]; then
      if _keys_listed FAL_KEY "$mk"; then
        missing+=" FAL_KEY"
        fal_missing=1
      fi
      continue
    fi
    if [ "${_KEYS_PRE[$n]:-absent}" = set ] || _keys_listed "$n" "$KEYS_CHANGED"; then continue; fi
    missing+=" $n"
    if [ "$n" = FAL_KEY ]; then fal_missing=1; fi
  done
  KEYS_MISSING=${missing# }
  if [ "$backend" = fal ] && [ "$fal_missing" = 1 ]; then
    KEYS_FAL_MISSING=1
    if ! _keys_listed FAL_KEY "$_KEYS_WOULD_ASK"; then _keys_fal_warning; fi
  fi
  if [ "$DRY_RUN" = 1 ]; then label="keys (dry run)"; fi
  local c k m
  c=$(_keys_fold "$KEYS_CHANGED")
  k=$(_keys_fold "$KEYS_KEPT")
  m=$(_keys_fold "$KEYS_MISSING")
  info "$label: changed: ${c:--} | kept: ${k:--} | missing: ${m:--}"
  return 0
}

# _keys_merge FILE BACKEND: the single stackenv.py merge (keys on stdin, plus the fal preset), then the report.
_keys_merge() {
  local file=$1 backend=$2 tpl="" preset="" shown_preset="the c101 checkout: config/stack.env.fal.example"
  local used=0 out="" rc=0 n backup="" overwritten mk="" target="" nb=""
  local -a args=(merge --file "$file")
  if [ -n "${C101_SRC:-}" ]; then
    tpl=$C101_SRC/config/stack.env.example
    preset=$C101_SRC/config/stack.env.fal.example
    shown_preset=$(tilde "$preset")
  fi
  if [ -n "$tpl" ] && [ -r "$tpl" ]; then args+=(--template "$tpl"); fi
  if [ "$backend" = fal ]; then
    if [ -n "$preset" ] && [ -r "$preset" ]; then
      used=1
      args+=(--preset "$preset" --placeholder your-fal-key --preset-key FAL_KEY)
    elif [ "$DRY_RUN" = 1 ]; then
      info "would merge the fal image preset from $shown_preset (not on disk yet)"
    else
      warn "the fal preset ($shown_preset) is not on disk (no c101 checkout yet?): the fal image backend was not configured; point --source at your c101 checkout ($(keys_rerun_hint "" "" "--source PATH")), or run the full install first"
    fi
  fi
  if [ "${KEYS_OVERWRITE:-0}" = 1 ]; then args+=(--overwrite); fi
  if [ "$DRY_RUN" = 1 ]; then args+=(--dry-run); fi

  # No key and no preset: nothing to merge, unless the file must be tightened to mode 600 (the merge does only that).
  if [ "${#_KEYS_NAMES[@]}" -eq 0 ] && [ "$used" = 0 ] && [ "$_KEYS_FIX_MODE" != 1 ]; then
    info "no key was supplied: $(tilde "$file") is unchanged"
    KEYS_RESULT="nochange"
    _keys_report "$backend" 0 ""
    return 0
  fi

  out=$(helper stackenv.py "${args[@]}" < <(_keys_emit_pairs)) || rc=$?
  _keys_forget
  case $rc in
    0) ;;
    4)
      _keys_readonly_help "$file" "$(_keys_json_word "$out" reason)"
      KEYS_RESULT="readonly"
      return 0
      ;;
    *)
      warn "$(tilde "$file") was not changed: stackenv.py failed (exit $rc; its message above names the cause, never a value)"
      if [ "$(_keys_json_word "$out" error)" = line-breaks ]; then
        info "It holds a bare CR, NUL, VT, FF or a Unicode line separator: c101's bash readers (bin/mcp-run, bin/mcp-headers) and its Python readers would see different lines. Remove it (CRLF alone is fine: it is converted), then re-run $(keys_rerun_hint)"
      fi
      KEYS_RESULT="failed"
      return 0
      ;;
  esac
  target=$(json_field "$out" target 2>/dev/null) || target=""
  if [ -z "$target" ]; then target=$file; fi

  KEYS_CHANGED=$(_keys_json_list "$out" changed)
  KEYS_KEPT=$(_keys_json_list "$out" kept)
  KEYS_KEPT_OPS=$(_keys_json_list "$out" kept_ops)
  overwritten=$(_keys_json_list "$out" overwritten_ops)
  mk=$(_keys_json_list "$out" missing_keys)

  if [ "$DRY_RUN" = 1 ]; then
    KEYS_RESULT="dry-run"
    if _keys_json_true "$out" would_write && [ ! -e "$file" ]; then
      info "would create $(tilde "$file") (mode 600) from c101's stack.env.example"
    fi
    for n in $KEYS_CHANGED; do info "would set $n"; done
    if [ -z "$KEYS_CHANGED" ]; then info "nothing would change in $(tilde "$file")"; fi
  else
    KEYS_RESULT="ok"
    if _keys_json_true "$out" wrote; then
      if _keys_json_true "$out" created; then
        ok "created $(tilde "$file") (mode 600)"
      else
        ok "updated $(tilde "$file") (mode 600)"
      fi
      backup=$(json_field "$out" backup 2>/dev/null) || backup=""
      if [ -n "$backup" ]; then
        info "previous version: $(tilde "$backup") (mode 600)"
        nb=$(_keys_json_int "$out" backups)
        if [ -n "$nb" ] && [ "$nb" -gt 0 ]; then
          info "$nb ${target##*/}.bak-* file(s) now sit in $(tilde "${target%/*}"), each a plain-text copy of your keys. The installer never deletes them: remove the ones you no longer need by hand, and keep them out of git if that directory is a dotfiles repository (git-ignore ${target##*/}.bak-*)"
        fi
      fi
    else
      ok "$(tilde "$file") already up to date"
    fi
  fi
  # Line endings and mode (by path only).
  if _keys_json_true "$out" crlf_normalized; then
    if [ "$DRY_RUN" = 1 ]; then
      info "would convert the CRLF line endings of $(tilde "$target") to LF (bin/mcp-run and bin/mcp-headers keep a CR, so a quoted value would read back with its quotes)"
    else
      info "converted the CRLF line endings of $(tilde "$target") to LF (bin/mcp-run and bin/mcp-headers keep a CR, so a quoted value read back with its quotes)"
    fi
  fi
  if [ "$_KEYS_FIX_MODE" = 1 ]; then
    if [ "$DRY_RUN" = 1 ]; then
      info "would tighten $(tilde "$target") to mode 600 (it is ${_KEYS_CHECK_MODE:-?}: group or others can read the keys)"
    elif _keys_json_true "$out" mode_fixed; then
      ok "tightened $(tilde "$target") to mode 600 (it was ${_KEYS_CHECK_MODE:-?}: group or others could read the keys)"
    else
      warn "$(tilde "$target") has mode ${_KEYS_CHECK_MODE:-?} and could not be tightened: group or others can read your keys. Fix: chmod 600 $(tilde "$target")"
    fi
  fi
  local kept_verb=kept switch_verb=switched
  if [ "$DRY_RUN" = 1 ]; then
    kept_verb="would keep"
    switch_verb="would switch"
  fi
  if [ -n "$KEYS_KEPT" ]; then
    info "$kept_verb the existing value of: $(_keys_fold "$KEYS_KEPT") (a different value was offered; --keys-overwrite replaces it)"
  fi
  if [ -n "$KEYS_KEPT_OPS" ]; then
    info "image operations $kept_verb on their current non-fal provider (kept_ops): $KEYS_KEPT_OPS (--keys-overwrite switches them to fal, except operations whose IMAGE_<OP>_* came from the environment)"
  fi
  if [ -n "$overwritten" ]; then
    info "image operations $switch_verb to the fal preset (--keys-overwrite): $overwritten"
  fi
  _keys_report "$backend" "$used" "$mk"
  return 0
}

# _keys_dry_plan_bash MODE BACKEND: a dry run on a host with no Python yet shows the plan from bash alone.
_keys_dry_plan_bash() {
  local n
  if [ "$1" != 0 ]; then _keys_collect_env; fi
  for n in ${_KEYS_NAMES[@]+"${_KEYS_NAMES[@]}"}; do info "would set $n"; done
  _keys_forget
  case $1 in prompt | auto) info "would ask (hidden input) for each key still missing, when run on a terminal" ;; esac
  if [ "$2" = fal ]; then info "would merge the fal image preset (IMAGE_<OP>_* lines) from c101's config/stack.env.fal.example"; fi
  info "(no Python yet, so the existing file was not inspected)"
  KEYS_RESULT="dry-run"
  return 0
}

# ---- entry points ----------------------------------------------------------------------------------------------------

_keys_run() {
  local mode backend file out="" rc=0
  keys_validate
  mode=$(_keys_mode)
  backend=$(_keys_backend)
  file=$(keys_stack_env_path)
  KEYS_STACK_ENV=$file
  KEYS_RESULT="" KEYS_FAL_MISSING=0 KEYS_CHANGED="" KEYS_KEPT="" KEYS_KEPT_OPS="" KEYS_MISSING=""
  _KEYS_PRE=()
  _KEYS_WOULD_ASK=""
  _keys_forget
  _keys_backend_effective "$backend"

  step "API keys and image backend: $(tilde "$file")"
  if [ "$_KEYS_ONLY" = 1 ]; then info "--keys-only: only stack.env is touched (no c101 installer, no other overlay step)"; fi
  info "keys=$mode image-backend=$backend overwrite=${KEYS_OVERWRITE:-0}"
  _KEYS_FIX_MODE=0 _KEYS_CHECK_MODE=""
  if [ "$mode" = 0 ] && [ "$backend" != fal ]; then
    info "key step off (KEYS=0) and image backend '$backend': stack.env is not touched"
    _keys_perm_warn "$file"
    KEYS_RESULT="skipped"
    return 0
  fi
  if ! _keys_python; then
    _keys_perm_warn "$file"
    if [ "$DRY_RUN" = 1 ]; then
      _keys_dry_plan_bash "$mode" "$backend"
      return 0
    fi
    if [ "$backend" = fal ]; then
      _KEYS_FAL_WARNED=1
      warn "no Python 3.9+ for lib/stackenv.py: FAL_KEY, the fal image preset and the other keys were not written, so fal image generation stays off; once Python or uv is installed, re-run $(keys_rerun_hint "" FAL_KEY)"
    else
      warn "no Python 3.9+ for lib/stackenv.py: keys and image backend skipped; once Python or uv is installed, re-run $(keys_rerun_hint)"
    fi
    KEYS_RESULT="skipped"
    return 0
  fi

  # A target that cannot be rewritten (a Nix store link, a read-only mount): say so before asking for anything.
  out=$(helper stackenv.py check --file "$file") || rc=$?
  if [ "$rc" = 4 ]; then
    local reason
    reason=$(_keys_json_word "$out" reason)
    _keys_readonly_help "$file" "$reason"
    case $reason in nix-store | read-only-mount) ;; *) _keys_check_report "$out" 0 || true ;; esac
    KEYS_RESULT="readonly"
    return 0
  fi
  # Owner, mode and directory of the target (SEC-3, SEC-10): another account's file is neither asked for nor written;
  # a group/other-readable file of yours is tightened by the merge below (also when nothing else changes).
  if ! _keys_check_report "$out" 1; then
    KEYS_RESULT="failed"
    return 0
  fi

  _keys_load_status "$file"
  if [ "$mode" != 0 ]; then _keys_collect_env; fi
  case $mode in
    prompt)
      if [ -t 0 ]; then
        _keys_prompt_all
      else
        warn "--keys: stdin is not a terminal, so no key is asked for; keys come from the environment only"
      fi
      ;;
    auto)
      if [ -t 0 ] && [ "${ASSUME_YES:-0}" != 1 ]; then
        _keys_prompt_all
      else
        info "not interactive: no key prompt (keys from the environment only; --keys asks on a terminal)"
      fi
      ;;
    *) ;; # 0 and env: no prompt is reachable
  esac
  _keys_merge "$file" "$backend"
  return 0
}

keys_step() {
  local xt=0
  case $- in *x*)
    xt=1
    set +x
    ;;
  esac
  _keys_run
  _keys_forget
  keys_scrub_env
  # Back to the caller's tracing only now that no value is held any more.
  if [ "$xt" = 1 ]; then set -o xtrace; fi
  return 0
}

keys_only() {
  _KEYS_ONLY=1
  keys_step
  _KEYS_ONLY=0
  return 0
}
