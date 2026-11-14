# shellcheck shell=bash
# shellcheck disable=SC2034  # P_* values are consumed by the entry points
# profiles.sh: read config/profiles/NAME.conf (KEY=VALUE lines, whole-line # comments; never sourced) and
# combine it with command-line overrides into the effective plan.

PROFILE_KEYS="DESCRIPTION C101_FLAGS C101_PACKS PATH_INTEGRATION ENVIRONMENT_D SANDBOX PLAYWRIGHT_DEPS DESKTOP_CHECKS NO_SUDO ASSUME_YES LINK_BINS CONTAINER_RUNTIME PODMAN_SERVICES DOCKER_HOST_PODMAN PRESET KEYS IMAGE_BACKEND FAL_MCP PREWARM_MCP PLAYWRIGHT_BROWSER LEARN_TIMER"

# Defaults for the parity keys when a profile file omits them (a custom profile written before they existed); every
# shipped profile sets all of them. fal image generation is c101's main use, so it is on by default (D3); the steps
# that add systemd units or a browser download stay off unless a profile asks for them. PRESET defaults to empty:
# the profile's C101_PACKS then decides (empty = all packs). KEYS_OVERWRITE is never a profile key (--keys-overwrite).
PROFILE_DEFAULTS="KEYS=auto IMAGE_BACKEND=fal FAL_MCP=1 PREWARM_MCP=1 PLAYWRIGHT_BROWSER=0 LEARN_TIMER=0"

profile_list() {
  local f out=""
  for f in "$C101L_ROOT"/config/profiles/*.conf; do
    [ -e "$f" ] || continue
    f=${f##*/}
    out="$out ${f%.conf}"
  done
  printf '%s\n' "${out# }"
}

profile_valid_name() { [[ $1 =~ ^[a-z][a-z0-9-]*$ ]]; }

# profile_load NAME: sets P_<KEY> for every key; rejects unknown keys so a typo cannot silently do nothing.
profile_load() {
  local name=$1 f line key val k known
  profile_valid_name "$name" || usage_error "invalid profile name '$name'"
  f="$C101L_ROOT/config/profiles/$name.conf"
  [ -r "$f" ] || usage_error "unknown profile '$name' (available: $(profile_list))"
  for k in $PROFILE_KEYS; do printf -v "P_$k" '%s' ""; done
  for k in $PROFILE_DEFAULTS; do printf -v "P_${k%%=*}" '%s' "${k#*=}"; done
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in '' | '#'*) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    known=0
    for k in $PROFILE_KEYS; do [ "$k" = "$key" ] && known=1; done
    [ "$known" = 1 ] || die "$f: unknown key '$key'"
    printf -v "P_$key" '%s' "$val"
  done <"$f"
}

# profile_check_bool KEY...: P_<KEY> must be 0 or 1, so a typo in a profile file cannot silently switch a step off.
profile_check_bool() {
  local k v
  for k in "$@"; do
    v=P_$k
    case ${!v-} in 0 | 1) ;; *) die "profile '${PROFILE:-?}': $k must be 0 or 1 (found '${!v-}')" ;; esac
  done
}

# flag_add FLAG / flag_del FLAG: edit the token list in P_C101_FLAGS (exact token match).
flag_has() {
  local t
  for t in $P_C101_FLAGS; do [ "$t" = "$1" ] && return 0; done
  return 1
}
flag_add() { flag_has "$1" || P_C101_FLAGS="${P_C101_FLAGS:+$P_C101_FLAGS }$1"; }
flag_del() {
  local t out=""
  for t in $P_C101_FLAGS; do [ "$t" = "$1" ] || out="${out:+$out }$t"; done
  P_C101_FLAGS=$out
}

# c101_flag_string: the argument list handed to c101's install.sh (dry-run is appended by the caller).
c101_flag_string() {
  local s=$P_C101_FLAGS
  [ -n "$P_C101_PACKS" ] && s="${s:+$s }--packs $P_C101_PACKS"
  [ -n "${OPT_MCP:-}" ] && s="${s:+$s }--mcp $OPT_MCP"
  printf '%s\n' "$s"
}

# apply_overrides: fold command-line choices (variables set by the parser, empty = not given) into P_*.
apply_overrides() {
  [ -n "${OPT_PACKS:-}" ] && P_C101_PACKS=$OPT_PACKS
  case ${OPT_MAIN_AGENT:-} in 1) flag_add --main-agent ;; 0) flag_del --main-agent ;; esac
  case ${OPT_ENFORCE_MODELS:-} in 1) flag_add --enforce-models ;; 0) flag_del --enforce-models ;; esac
  [ -n "${OPT_PATH_INTEGRATION:-}" ] && P_PATH_INTEGRATION=$OPT_PATH_INTEGRATION
  [ -n "${OPT_ENVIRONMENT_D:-}" ] && P_ENVIRONMENT_D=$OPT_ENVIRONMENT_D
  [ -n "${OPT_SANDBOX:-}" ] && P_SANDBOX=$OPT_SANDBOX
  [ -n "${OPT_PLAYWRIGHT_DEPS:-}" ] && P_PLAYWRIGHT_DEPS=$OPT_PLAYWRIGHT_DEPS
  [ -n "${OPT_LINK_BINS:-}" ] && P_LINK_BINS=$OPT_LINK_BINS
  [ -n "${OPT_CONTAINER_RUNTIME:-}" ] && P_CONTAINER_RUNTIME=$OPT_CONTAINER_RUNTIME
  [ -n "${OPT_PODMAN_SERVICES:-}" ] && P_PODMAN_SERVICES=$OPT_PODMAN_SERVICES
  [ -n "${OPT_DOCKER_HOST_PODMAN:-}" ] && P_DOCKER_HOST_PODMAN=$OPT_DOCKER_HOST_PODMAN
  # Parity keys. PRESET is not folded here: presets_resolve (lib/presets.sh) combines the profile's PRESET and
  # C101_PACKS with --preset/--pick-preset/--packs (a command-line selection replaces the profile's, D9).
  [ -n "${OPT_KEYS:-}" ] && P_KEYS=$OPT_KEYS
  [ -n "${OPT_IMAGE_BACKEND:-}" ] && P_IMAGE_BACKEND=$OPT_IMAGE_BACKEND
  [ -n "${OPT_FAL_MCP:-}" ] && P_FAL_MCP=$OPT_FAL_MCP
  [ -n "${OPT_PREWARM_MCP:-}" ] && P_PREWARM_MCP=$OPT_PREWARM_MCP
  [ -n "${OPT_PLAYWRIGHT_BROWSER:-}" ] && P_PLAYWRIGHT_BROWSER=$OPT_PLAYWRIGHT_BROWSER
  [ -n "${OPT_LEARN_TIMER:-}" ] && P_LEARN_TIMER=$OPT_LEARN_TIMER
  [ "${OPT_NO_SUDO:-}" = 1 ] && P_NO_SUDO=1
  [ "${OPT_YES:-}" = 1 ] && P_ASSUME_YES=1
  NO_SUDO=$P_NO_SUDO
  ASSUME_YES=$P_ASSUME_YES
  return 0
}

print_config() {
  printf 'PROFILE=%s\n' "$PROFILE"
  printf 'C101_FLAGS=%s\n' "$(c101_flag_string)"
  printf 'PATH_INTEGRATION=%s\n' "$P_PATH_INTEGRATION"
  printf 'ENVIRONMENT_D=%s\n' "$P_ENVIRONMENT_D"
  printf 'SANDBOX=%s\n' "$P_SANDBOX"
  printf 'PLAYWRIGHT_DEPS=%s\n' "$P_PLAYWRIGHT_DEPS"
  printf 'DESKTOP_CHECKS=%s\n' "$P_DESKTOP_CHECKS"
  printf 'LINK_BINS=%s\n' "$P_LINK_BINS"
  printf 'CONTAINER_RUNTIME=%s\n' "$P_CONTAINER_RUNTIME"
  printf 'PODMAN_SERVICES=%s\n' "$P_PODMAN_SERVICES"
  printf 'DOCKER_HOST_PODMAN=%s\n' "$P_DOCKER_HOST_PODMAN"
  printf 'MANAGED_SETTINGS=%s\n' "${OPT_MANAGED_SETTINGS:-0}"
  printf 'NO_SUDO=%s\n' "$NO_SUDO"
  printf 'ASSUME_YES=%s\n' "$ASSUME_YES"
  # Parity keys: the resolved preset and packs (presets_resolve plus the fal image preset; PACKS=all means every
  # pack), the key step's inputs and the opt-in steps. Names and fixed words only, never a key value.
  printf 'PRESET=%s\n' "${PRESET_RESOLVED-}"
  if [ "${PRESET_ALL:-1}" = 1 ]; then printf 'PACKS=all\n'; else printf 'PACKS=%s\n' "${PRESET_PACKS-}"; fi
  printf 'KEYS=%s\n' "${KEYS:-${P_KEYS:-0}}"
  printf 'KEYS_OVERWRITE=%s\n' "${KEYS_OVERWRITE:-0}"
  printf 'IMAGE_BACKEND=%s\n' "${IMAGE_BACKEND:-${P_IMAGE_BACKEND:-keep}}"
  printf 'FAL_MCP=%s\n' "$P_FAL_MCP"
  printf 'PREWARM_MCP=%s\n' "$P_PREWARM_MCP"
  printf 'PLAYWRIGHT_BROWSER=%s\n' "$P_PLAYWRIGHT_BROWSER"
  printf 'LEARN_TIMER=%s\n' "$P_LEARN_TIMER"
}
