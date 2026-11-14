# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points
# shellcheck disable=SC2016  # the printed command keeps ${XDG_RUNTIME_DIR} for the user's shell
# services.sh: what Podman needs to stand in for Docker's daemon: systemd user units (API socket, sandbox image
# refresh, prune), linger for SSH/headless boxes, and the optional `docker` shim package.
# Needs: common.sh, detect.sh, deps.sh.

UNIT_MARKER="# c101-linux managed unit"
SERVICE_UNITS_API="c101-podman-api.socket c101-podman-api.service"
SERVICE_UNITS_SANDBOX="c101-sandbox-image.service c101-sandbox-image.timer"
SERVICE_UNITS_PRUNE="c101-podman-prune.service c101-podman-prune.timer"

user_unit_dir() { printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"; }

# detect_init: INIT_SYSTEM = systemd | openrc | other ; SYSTEMD_USER_OK = 1 when `systemctl --user` can reach a manager.
detect_init() {
  INIT_SYSTEM=other
  SYSTEMD_USER_OK=0
  if [ -d /run/systemd/system ]; then
    INIT_SYSTEM=systemd
  elif have rc-service || [ -d /run/openrc ]; then
    INIT_SYSTEM=openrc
  fi
  if [ "$INIT_SYSTEM" = systemd ] && have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    SYSTEMD_USER_OK=1
  fi
}

# manual_api_command: what to run without systemd.
manual_api_command() {
  printf 'podman system service --time=0 "unix://${XDG_RUNTIME_DIR:-/run/user/%s}/podman/podman.sock" &\n' "$(id -u)"
}

# distro_has_podman_socket: the distro ships podman.socket as a user unit.
distro_has_podman_socket() {
  local out
  out=$(systemctl --user list-unit-files podman.socket 2>/dev/null || true) # no `| grep -q`: pipefail + early close = SIGPIPE
  [[ $out == *podman.socket* ]]
}

# docker_in_use: a real Docker is in use here: the user's own DOCKER_HOST, a docker daemon socket or service, or a
# non-default docker context. A `docker` that is podman's shim does not count.
docker_in_use() {
  local v ctx
  if [ -n "${DOCKER_HOST:-}" ] && [[ $DOCKER_HOST != *podman/podman.sock ]]; then return 0; fi
  if have docker; then
    v=$(docker --version 2>/dev/null || true)
    if [[ $v == *[Pp]odman* ]]; then return 1; fi
  fi
  local s
  for s in ${C101_LINUX_DOCKER_SOCKETS:-/var/run/docker.sock /run/docker.sock}; do
    if [ -S "$s" ]; then return 0; fi
  done
  if have systemctl && systemctl is-active --quiet docker 2>/dev/null; then return 0; fi
  if have docker; then
    ctx=$(docker context show 2>/dev/null || true)
    if [ -n "$ctx" ] && [ "$ctx" != default ]; then return 0; fi
  fi
  return 1
}

# docker_host_decision MODE(auto|1|0): sets ENV_DOCKER_API, i.e. whether the profile block and environment.d point
# DOCKER_HOST at Podman's socket. Auto: only with a working user systemd API unit and no real Docker in use.
docker_host_decision() {
  case $1 in
    0) ENV_DOCKER_API=0 ;;
    1) ENV_DOCKER_API=1 ;;
    *)
      ENV_DOCKER_API=0
      if [ "${SYSTEMD_USER_OK:-0}" != 1 ]; then
        next_step "No systemd user manager, so DOCKER_HOST was not exported. After starting the Podman API by hand, export DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/podman/podman.sock or re-run $(rerun_cmd --docker-host-podman)"
      elif docker_in_use; then
        next_step "A Docker daemon, context or DOCKER_HOST is already in use here, so DOCKER_HOST was NOT pointed at Podman (that would hide your Docker containers). To switch anyway: re-run $(rerun_cmd --docker-host-podman)"
      else
        ENV_DOCKER_API=1
      fi
      ;;
  esac
}

# install_unit NAME [RUNTIME]: copy systemd/user/NAME into the user unit dir (marker-guarded, backed up when it differs).
install_unit() {
  local name=$1 runtime=${2:-} src dest tmp res
  src=$C101L_ROOT/systemd/user/$name
  dest=$(user_unit_dir)/$name
  [ -r "$src" ] || die "missing unit file $src"
  if dry_plan_only; then
    info "dry-run (no Python yet, plan only): would install unit $name -> $(tilde "$dest")"
    return 0
  fi
  tmp=$(tmp_file unit)
  if [ -n "$runtime" ] && [ "$name" = c101-sandbox-image.service ]; then
    sed "s#^Environment=DESKTOP_SANDBOX_RUNTIME=.*#Environment=DESKTOP_SANDBOX_RUNTIME=${runtime}#" "$src" >"$tmp"
  else
    cp -- "$src" "$tmp"
  fi
  # shellcheck disable=SC2046  # the dry-run flag is absent or one word
  res=$(helper managed_block.py write-file --file "$dest" --content-file "$tmp" --marker "$UNIT_MARKER" $(_helper_dry_flag))
  if [ "$(json_field "$res" changed)" = true ]; then info "unit $name -> $(tilde "$dest")"; else info "unit $name already current"; fi
  state_append systemd_units "$(json_str "$dest")"
  rm -f -- "$tmp"
}

# sd_user CMD...: systemctl --user, dry-run aware.
sd_user() { run systemctl --user "$@"; }

# install_podman_services TIMERS(0|1) RUNTIME_VALUE
install_podman_services() {
  local timers=${1:-0} runtime=${2:-} fallback=0 u api_unit prev
  detect_init
  if [ "$SYSTEMD_USER_OK" != 1 ]; then
    case $INIT_SYSTEM in
      systemd) warn "systemd is running but 'systemctl --user' is unreachable (no user session: ssh without a login session, su, a container). Run 'loginctl enable-linger $(id -un)' as root, log in again, and re-run $(rerun_cmd --podman-services)." ;;
      openrc) warn "OpenRC system: no systemd user units. Docker-compatible API by hand:" ;;
      *) warn "no systemd here (Alpine/OpenRC, Void, WSL without systemd=true, or a container): units skipped. Docker-compatible API by hand:" ;;
    esac
    info "$(manual_api_command)"
    info "(add it to your session start, e.g. an OpenRC user service or your shell profile; docs/SCENARIOS.md)"
    next_step "No systemd user manager: start the Podman API yourself: $(manual_api_command)"
    return 0
  fi
  step "Podman services (systemd user units)"
  run mkdir -p -- "$(user_unit_dir)"
  if distro_has_podman_socket; then
    info "distro podman.socket found: using it for the Docker-compatible API"
  else
    fallback=1
    for u in $SERVICE_UNITS_API; do install_unit "$u"; done
  fi
  for u in $SERVICE_UNITS_SANDBOX; do install_unit "$u" "$runtime"; done
  for u in $SERVICE_UNITS_PRUNE; do install_unit "$u"; done
  sd_user daemon-reload
  if [ "$fallback" = 1 ]; then
    api_unit=c101-podman-api.socket
    sd_user enable --now "$api_unit"
    state_set podman_api_unit "$(json_str "$api_unit")"
  else
    api_unit=podman.socket
    prev=$(state_get podman_api_unit 2>/dev/null || true)
    if [ -z "$prev" ] && systemctl --user is-enabled --quiet podman.socket 2>/dev/null; then
      info "podman.socket was already enabled; the uninstaller will leave it enabled"
      state_set podman_api_unit '"podman.socket (preexisting)"'
    elif [[ $prev == *preexisting* ]]; then
      info "podman.socket was enabled before this overlay; left alone"
    else
      sd_user enable --now podman.socket
      state_set podman_api_unit '"podman.socket"'
    fi
  fi
  if [ "$timers" = 1 ]; then
    sd_user enable --now c101-sandbox-image.timer c101-podman-prune.timer
    state_set podman_timers true
    ok "weekly timers enabled: c101-sandbox-image.timer, c101-podman-prune.timer"
  else
    info "timers installed but not enabled (--podman-timers): systemctl --user enable --now c101-sandbox-image.timer c101-podman-prune.timer"
  fi
  if [ "$DRY_RUN" != 1 ]; then
    if systemctl --user is-active --quiet "$api_unit"; then
      ok "Docker-compatible API: unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"
    else
      warn "the Podman API socket is not active yet; check: systemctl --user status $api_unit"
    fi
  fi
  next_step "Docker-API clients (compose v2, testcontainers, IDEs) can use the Podman socket: DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/podman/podman.sock (exported for you unless a real Docker is in use)."
}

# enable_linger: user units run without a login session (SSH/headless). Root (or polkit) required.
enable_linger() {
  local user
  user=$(id -un)
  if have loginctl && [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null)" = yes ]; then
    ok "linger already enabled for $user"
    return 0
  fi
  run_priv loginctl enable-linger "$user" || warn "loginctl enable-linger failed"
  if [ "$PRIV_DEFERRED" = 1 ]; then
    next_step "Enable linger so user units run without a login session: sudo loginctl enable-linger $user"
  elif [ "$DRY_RUN" != 1 ] && [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null)" = yes ]; then
    state_set linger_enabled_by_overlay true
  else
    warn "linger is still off for $user"
  fi
}

# install_docker_shim: the distro's podman-docker package ('docker' command that runs podman). Never next to real Docker.
install_docker_shim() {
  step "docker command shim (podman-docker)"
  if have docker && ! docker --version 2>/dev/null | grep -qi podman; then
    warn "a real Docker is installed ($(command -v docker)); podman-docker conflicts with it. Not installing the shim."
    return 0
  fi
  if have docker; then
    ok "docker already resolves to podman ($(command -v docker))"
    return 0
  fi
  pkg_install podman-docker || {
    warn "could not install podman-docker"
    return 0
  }
  if [ "$PRIV_DEFERRED" != 1 ] && [ "$DRY_RUN" != 1 ]; then
    state_set docker_shim_installed true
    ok "docker -> podman (package podman-docker); remove with your package manager if you want it gone"
  fi
}

# remove_podman_services: used by uninstall-linux.sh; only units listed in the state file.
remove_podman_services() {
  local units f name api
  units=$(state_get systemd_units 2>/dev/null || true)
  api=$(state_get podman_api_unit 2>/dev/null || true)
  detect_init
  if [ "$SYSTEMD_USER_OK" = 1 ]; then
    if [ -n "$units" ]; then
      while IFS= read -r f; do
        [ -n "$f" ] || continue
        name=${f##*/}
        case $name in *.socket | *.timer) run systemctl --user disable --now "$name" 2>/dev/null || true ;; esac
      done < <(helper_list "$units")
    fi
    # The distro's own podman.socket is only turned off when this overlay turned it on.
    if [ "$api" = podman.socket ]; then run systemctl --user disable --now podman.socket 2>/dev/null || true; fi
  fi
  if [ -n "$units" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      # shellcheck disable=SC2046
      helper managed_block.py remove-file --file "$f" --marker "$UNIT_MARKER" $(_helper_dry_flag) >/dev/null && info "removed $(tilde "$f")"
    done < <(helper_list "$units")
    if [ "$SYSTEMD_USER_OK" = 1 ]; then run systemctl --user daemon-reload || true; fi
  fi
}
