# shellcheck shell=bash
# shellcheck disable=SC2034  # P_* values are consumed by the entry points
# profiles.sh: read config/profiles/NAME.conf (KEY=VALUE lines, whole-line # comments; never sourced) and
# combine it with command-line overrides into the effective plan.

PROFILE_KEYS="DESCRIPTION C101_FLAGS C101_PACKS PATH_INTEGRATION ENVIRONMENT_D SANDBOX PLAYWRIGHT_DEPS DESKTOP_CHECKS NO_SUDO ASSUME_YES LINK_BINS CONTAINER_RUNTIME PODMAN_SERVICES DOCKER_HOST_PODMAN"

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
}
