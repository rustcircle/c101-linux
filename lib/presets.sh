# shellcheck shell=bash
# shellcheck disable=SC2034  # PRESET_* results are read by install-linux.sh, the doctor and the tests
# presets.sh: named agent/skill presets (config/presets.conf) on top of c101's install packs (--packs).
# Sourced after lib/common.sh (uses C101L_ROOT, usage_error, die, info, tilde, helper, helper_python_available).
# Pure bash >= 4, safe under set -euo pipefail; only presets_validate_packs runs Python (lib/packs.py).
#
# ---- Contract for install-linux.sh (node I1) ---------------------------------------------------------------------
# Inputs as I1 wires them:
#   P_PRESET         profile key PRESET (add it to PROFILE_KEYS): comma list of preset names, may be empty
#   P_C101_PACKS     profile key C101_PACKS after apply_overrides (equals OPT_PACKS when --packs was given)
#   OPT_PRESET       all --preset values joined by commas (repeatable): OPT_PRESET="${OPT_PRESET:+$OPT_PRESET,}$2"
#   OPT_PACKS        --packs a,b (existing flag)
#   OPT_PICK_PRESET  1 after --pick-preset, else 0
#
# presets_resolve PROFILE_PRESET PROFILE_PACKS CLI_PRESET CLI_PACKS PICK
#     presets_resolve "$P_PRESET" "$P_C101_PACKS" "$OPT_PRESET" "$OPT_PACKS" "$OPT_PICK_PRESET"
#   Call it directly, never inside $(...) (usage_error must end the installer), after profile_load and
#   apply_overrides and before print_config. Lists may be separated by commas and/or spaces; duplicates are dropped.
#   Selection rule:
#   - The command line is one selection: the union of every --preset value, the presets picked with
#     --pick-preset and --packs. When it selects anything it REPLACES the profile's PRESET + C101_PACKS (exactly as
#     --packs already replaces C101_PACKS); otherwise the profile's PRESET and C101_PACKS apply, unioned.
#     (A plain union with the profile would turn every --preset/--packs into a no-op on the profiles whose
#     PRESET is everything.)
#   - Any preset with an empty pack list (everything), or the pack name 'all', selects all packs.
#   - Nothing selected at all means all packs (c101's own default).
#   Errors, all usage_error (exit 2): unknown preset, a name that is not [a-z0-9][a-z0-9-]*, --pick-preset with
#   no TTY on stdin. Pack existence is checked later by presets_validate_packs (needs the c101 checkout).
#   Results (globals):
#     PRESET_PACKS     comma pack list for c101 --packs, core first; EMPTY = all packs
#     PRESET_ALL       1 when PRESET_PACKS is empty (all packs), else 0
#     PRESET_RESOLVED  preset names applied, comma list in first-seen order; 'everything' when PRESET_ALL=1;
#                      empty when only --packs / C101_PACKS chose packs (state key 'preset')
#     PRESET_SOURCE    cli | profile: which layer was used
#     PRESETS_PICKED   what --pick-preset returned (comma list; empty when Enter kept the default)
#
# presets_add_pack NAME...      add packs to a non-empty list; a no-op when PRESET_ALL=1 (all packs include it).
#                               I1, for an effective image backend of fal: presets_add_pack image
# presets_add_preset NAME       add a whole preset (its packs, and its name to PRESET_RESOLVED); a preset with an
#                               empty list switches to all packs. D3's "the image pack (image + graphic-design)" is
#                               presets_add_preset image.
#   Then, once, after the add_* calls:  P_C101_PACKS=$PRESET_PACKS
#   (c101_flag_string passes --packs only when it is non-empty, so all packs needs no change there.)
# presets_validate_packs [JSON] check every pack name met so far (presets, --packs, add_*) against JSON (default
#                               $C101_SRC/config/packs.json) with lib/packs.py; unknown -> usage_error (exit 2).
#                               Call after fetch_source and before c101_run. It skips with an info line when JSON is
#                               not on disk yet (dry run before a clone) or, in a dry run, when there is no Python.
# presets_pick [KEEP_LABEL]     numbered menu on a TTY; sets PRESETS_PICKED. presets_resolve calls it when PICK=1.
# presets_names                 print the preset names, comma-separated (usage text, messages)
# presets_summary               print one line: preset=<PRESET_RESOLVED or -> packs=<PRESET_PACKS or all> (<source>)
# State (I1): state_set preset "$(json_str "$PRESET_RESOLVED")"; state_set packs "$(json_str "$PRESET_PACKS")"
# Test hook: C101L_PRESETS_FILE replaces config/presets.conf.
# ------------------------------------------------------------------------------------------------------------------

PRESET_PACKS=""
PRESET_ALL=1
PRESET_RESOLVED=""
PRESET_SOURCE=""
PRESETS_PICKED=""

_PRESET_NAMES=()     # presets.conf, in file order
_PRESET_LISTS=()     # their pack lists (empty = all packs)
_PRESET_DESCS=()     # their descriptions
_PRESETS_LOADED=0
_PRESET_IDX=-1       # set by _presets_find
_PRESET_NORM=""      # set by _presets_norm
_PRESET_SEEN=""      # every pack name met so far, for presets_validate_packs
_R_PRESETS="" _R_PACKS="" _R_ALL=0

presets_file() { printf '%s\n' "${C101L_PRESETS_FILE:-$C101L_ROOT/config/presets.conf}"; }

_presets_name_ok() { [[ $1 =~ ^[a-z0-9][a-z0-9-]*$ ]]; }

# _presets_union VAR ITEM: append ITEM to the comma list held in VAR unless it is already there.
_presets_union() {
  local _u_cur=${!1}
  case ",$_u_cur," in *",$2,"*) return 0 ;; esac
  printf -v "$1" '%s' "${_u_cur:+$_u_cur,}$2"
}

# presets_load: parse presets.conf once. A malformed file is an installer bug, so it dies (exit 1).
presets_load() {
  if [ "$_PRESETS_LOADED" = 1 ]; then return 0; fi
  local f line name rest packs desc n=0 i
  f=$(presets_file)
  [ -r "$f" ] || die "presets file not found: $f"
  _PRESET_NAMES=()
  _PRESET_LISTS=()
  _PRESET_DESCS=()
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    case $line in '' | '#'*) continue ;; *'|'*'|'*) ;; *) die "$f:$n: expected name|packs|description" ;; esac
    name=${line%%|*}
    rest=${line#*|}
    packs=${rest%%|*}
    desc=${rest#*|}
    [[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "$f:$n: invalid preset name '$name'"
    [[ $packs =~ ^([a-z0-9][a-z0-9-]*(,[a-z0-9][a-z0-9-]*)*)?$ ]] || die "$f:$n: preset '$name': packs must be a comma list of pack names without spaces"
    for ((i = 0; i < ${#_PRESET_NAMES[@]}; i++)); do
      if [ "${_PRESET_NAMES[i]}" = "$name" ]; then die "$f:$n: duplicate preset '$name'"; fi
    done
    _PRESET_NAMES+=("$name")
    _PRESET_LISTS+=("$packs")
    _PRESET_DESCS+=("$desc")
  done <"$f"
  [ "${#_PRESET_NAMES[@]}" -gt 0 ] || die "$f defines no presets"
  _PRESETS_LOADED=1
}

# _presets_find NAME: set _PRESET_IDX to the preset's index; return 1 when there is no such preset.
_presets_find() {
  local i
  _PRESET_IDX=-1
  for ((i = 0; i < ${#_PRESET_NAMES[@]}; i++)); do
    if [ "${_PRESET_NAMES[i]}" = "$1" ]; then
      _PRESET_IDX=$i
      return 0
    fi
  done
  return 1
}

presets_names() {
  presets_load
  local n out=""
  for n in "${_PRESET_NAMES[@]}"; do out="${out:+$out, }$n"; done
  printf '%s\n' "$out"
}

# _presets_norm KIND WHAT LIST: check LIST (commas and/or whitespace) and set _PRESET_NORM to the de-duplicated
# comma list. KIND preset: each name must exist in presets.conf. KIND pack: syntax only (packs.py checks existence).
_presets_norm() {
  local kind=$1 what=$2 t
  local -a raw=()
  _PRESET_NORM=""
  IFS=$', \t\n' read -r -d '' -a raw <<<"$3" || true
  for t in ${raw[@]+"${raw[@]}"}; do
    [ -n "$t" ] || continue
    _presets_name_ok "$t" || usage_error "$what: invalid $kind name '$t' (lowercase letters, digits and '-')"
    if [ "$kind" = preset ] && ! _presets_find "$t"; then
      usage_error "$what: unknown preset '$t' (presets: $(presets_names))"
    fi
    _presets_union _PRESET_NORM "$t"
  done
  return 0
}

_presets_take_pack() { # NAME: into the selection being resolved
  if [ "$1" = all ]; then
    _R_ALL=1
    return 0
  fi
  _presets_union _R_PACKS "$1"
  _presets_union _PRESET_SEEN "$1"
}

_presets_take_preset() { # NAME (already checked): its name and packs into the selection being resolved
  local list p
  local -a ps=()
  _presets_find "$1" || usage_error "unknown preset '$1' (presets: $(presets_names))"
  list=${_PRESET_LISTS[_PRESET_IDX]}
  _presets_union _R_PRESETS "$1"
  if [ -z "$list" ]; then
    _R_ALL=1
    return 0
  fi
  IFS=, read -r -a ps <<<"$list"
  for p in "${ps[@]}"; do _presets_take_pack "$p"; done
  return 0
}

_presets_set_all() {
  PRESET_ALL=1
  PRESET_PACKS=""
  PRESET_RESOLVED=everything
}

presets_resolve() {
  local profile_preset=${1:-} profile_packs=${2:-} cli_preset=${3:-} cli_packs=${4:-} pick=${5:-0}
  local sel_presets sel_packs keep t
  local -a parts=()
  presets_load
  _PRESET_SEEN=""
  PRESETS_PICKED=""
  # Check both layers (command line first, so a bad --packs is reported as --packs), so a typo never passes silently.
  _presets_norm preset --preset "$cli_preset"
  cli_preset=$_PRESET_NORM
  _presets_norm pack --packs "$cli_packs"
  cli_packs=$_PRESET_NORM
  _presets_norm preset "profile PRESET" "$profile_preset"
  profile_preset=$_PRESET_NORM
  _presets_norm pack "profile C101_PACKS" "$profile_packs"
  profile_packs=$_PRESET_NORM

  if [ "$pick" = 1 ]; then
    if [ -n "$cli_preset$cli_packs" ]; then
      keep="only${cli_preset:+ --preset $cli_preset}${cli_packs:+ --packs $cli_packs}"
    else
      keep="the profile default (${profile_preset:-${profile_packs:-everything}})"
    fi
    presets_pick "$keep"
  fi

  if [ -n "$cli_preset$cli_packs$PRESETS_PICKED" ]; then
    PRESET_SOURCE=cli
    sel_presets=$cli_preset
    IFS=, read -r -a parts <<<"$PRESETS_PICKED"
    for t in ${parts[@]+"${parts[@]}"}; do _presets_union sel_presets "$t"; done
    sel_packs=$cli_packs
  else
    PRESET_SOURCE=profile
    sel_presets=$profile_preset
    sel_packs=$profile_packs
  fi

  _R_PRESETS=""
  _R_PACKS=""
  _R_ALL=0
  parts=()
  IFS=, read -r -a parts <<<"$sel_presets"
  for t in ${parts[@]+"${parts[@]}"}; do _presets_take_preset "$t"; done
  parts=()
  IFS=, read -r -a parts <<<"$sel_packs"
  for t in ${parts[@]+"${parts[@]}"}; do _presets_take_pack "$t"; done
  if [ -z "$_R_PRESETS$_R_PACKS" ]; then _R_ALL=1; fi

  if [ "$_R_ALL" = 1 ]; then
    _presets_set_all
    return 0
  fi
  PRESET_ALL=0
  PRESET_PACKS=core
  parts=()
  IFS=, read -r -a parts <<<"$_R_PACKS"
  for t in ${parts[@]+"${parts[@]}"}; do _presets_union PRESET_PACKS "$t"; done
  PRESET_RESOLVED=$_R_PRESETS
  return 0
}

presets_add_pack() {
  local p
  for p in "$@"; do
    _presets_name_ok "$p" || usage_error "invalid pack name '$p'"
    if [ "$p" = all ]; then
      _presets_set_all
      continue
    fi
    _presets_union _PRESET_SEEN "$p"
    if [ "$PRESET_ALL" != 1 ]; then _presets_union PRESET_PACKS "$p"; fi
  done
  return 0
}

presets_add_preset() {
  local name=${1:-} list p
  local -a ps=()
  presets_load
  _presets_find "$name" || usage_error "unknown preset '$name' (presets: $(presets_names))"
  list=${_PRESET_LISTS[_PRESET_IDX]}
  if [ "$PRESET_ALL" = 1 ]; then return 0; fi
  if [ -z "$list" ]; then
    _presets_set_all
    return 0
  fi
  _presets_union PRESET_RESOLVED "$name"
  IFS=, read -r -a ps <<<"$list"
  for p in "${ps[@]}"; do
    _presets_union _PRESET_SEEN "$p"
    _presets_union PRESET_PACKS "$p"
  done
  return 0
}

# presets_pick [KEEP_LABEL]: numbered menu on stderr, answer from the TTY on stdin. Numbers or names, separated by
# commas or spaces; Enter picks nothing (KEEP_LABEL says what then applies). Three bad answers, end of input or no TTY
# end in usage_error.
presets_pick() {
  local keep=${1:-the profile default} answer tok idx i n tries=0 bad picked
  local -a toks=()
  PRESETS_PICKED=""
  [ -t 0 ] || usage_error "--pick-preset needs an interactive terminal on stdin; use --preset NAME[,NAME] instead (presets: $(presets_names))"
  presets_load
  n=${#_PRESET_NAMES[@]}
  {
    printf '\nAgent and skill presets (core is always installed; combine several, e.g. 1,3):\n'
    for ((i = 0; i < n; i++)); do
      printf '  %2d) %-10s  %s\n' "$((i + 1))" "${_PRESET_NAMES[i]}" "${_PRESET_DESCS[i]}"
      printf '      %-10s  packs: %s\n' '' "${_PRESET_LISTS[i]:-all}"
    done
  } >&2
  while [ "$tries" -lt 3 ]; do
    tries=$((tries + 1))
    answer=""
    if ! read -r -p "Presets [numbers or names; Enter keeps $keep]: " answer; then
      printf '\n' >&2
      usage_error "--pick-preset: no answer (end of input)"
    fi
    picked=""
    bad=""
    toks=()
    IFS=$', \t\n' read -r -d '' -a toks <<<"$answer" || true
    for tok in ${toks[@]+"${toks[@]}"}; do
      [ -n "$tok" ] || continue
      if [[ $tok =~ ^[0-9]{1,3}$ ]]; then
        idx=$((10#$tok - 1))
        if [ "$idx" -ge 0 ] && [ "$idx" -lt "$n" ]; then
          _presets_union picked "${_PRESET_NAMES[idx]}"
        else
          bad="${bad:+$bad, }$tok"
        fi
      elif _presets_name_ok "$tok" && _presets_find "$tok"; then
        _presets_union picked "$tok"
      else
        bad="${bad:+$bad, }$tok"
      fi
    done
    if [ -z "$bad" ]; then
      PRESETS_PICKED=$picked
      return 0
    fi
    printf '    not a preset: %s (pick 1-%d or a name)\n' "$bad" "$n" >&2
  done
  usage_error "--pick-preset: no valid choice after 3 tries"
}

presets_validate_packs() {
  local json=${1:-${C101_SRC:-}/config/packs.json} out="" known="" rc=0
  if [ -z "$_PRESET_SEEN" ]; then return 0; fi
  if [ ! -r "$json" ]; then
    info "pack names not checked yet: $(tilde "$json") is not on disk (c101 rejects unknown packs itself)"
    return 0
  fi
  if [ "${DRY_RUN:-0}" = 1 ] && ! helper_python_available; then
    info "pack names not checked: no Python for the helpers in this dry run"
    return 0
  fi
  out=$(helper packs.py --packs-json "$json" validate "$_PRESET_SEEN") || rc=$?
  case $rc in
    0) return 0 ;;
    2)
      if [ -n "$out" ]; then
        known=$(helper packs.py --packs-json "$json" list) || known=""
        usage_error "unknown pack(s): ${out//$'\n'/, } (c101 packs: ${known//$'\n'/, })"
      fi
      ;;
  esac
  die "could not check pack names against $(tilde "$json") (packs.py exit $rc)"
}

presets_summary() {
  local src=${PRESET_SOURCE:+ ($PRESET_SOURCE)}
  if [ "$PRESET_ALL" = 1 ]; then
    printf 'preset=%s packs=all%s\n' "${PRESET_RESOLVED:-everything}" "$src"
  else
    printf 'preset=%s packs=%s%s\n' "${PRESET_RESOLVED:--}" "$PRESET_PACKS" "$src"
  fi
}
