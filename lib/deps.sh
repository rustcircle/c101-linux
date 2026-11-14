# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points
# shellcheck disable=SC2016  # the sh -c script keeps $1.. for the inner shell
# deps.sh: prerequisites (curl, git), uv, Node, the container sandbox and Playwright system libraries.
# Needs: common.sh, detect.sh.  Privileged commands always go through run_priv (honours --no-sudo and --dry-run).

UV_VERSION=${C101L_UV_VERSION:-0.13.7}
NODE_MIN_MAJOR=18

# pkg_names TOOL: distro package names for a tool, per family.
pkg_names() {
  case $1:$OS_FAMILY in
    node:apt | node:dnf | node:pacman | node:apk) echo "nodejs npm" ;;
    node:zypper) echo "nodejs-default npm-default" ;;
    gnupg:apt | gnupg:pacman | gnupg:apk) echo "gnupg" ;;
    gnupg:dnf | gnupg:zypper) echo "gnupg2" ;;
    *) echo "$1" ;;
  esac
}

# nixos_module_snippet: what to put in configuration.nix for this overlay.
nixos_module_snippet() {
  cat <<'NIX_EOF'
    { pkgs, ... }: {
      programs.nix-ld.enable = true;                  # uv and the Python builds it downloads are dynamically linked
      environment.systemPackages = with pkgs; [ uv git curl nodejs ];
      virtualisation.podman = {                       # rootless podman + the Docker-compatible socket and `docker` alias
        enable = true;
        dockerCompat = true;                          # not together with virtualisation.docker
        defaultNetwork.settings.dns_enabled = true;
      };
      # users.users.<you>.isNormalUser = true;       # normal users get subuid/subgid ranges automatically
    }
NIX_EOF
}

# nixos_check: stock NixOS cannot run astral's uv or the CPython builds uv downloads (no ld.so for foreign ELF
# binaries). Require nixpkgs' uv and nix-ld instead of fetching binaries that cannot start; abort in a real run.
nixos_check() {
  local problem=""
  if ! have uv; then problem="uv is not installed (this installer does not fetch astral's binary on NixOS: it cannot run there)"; fi
  if [ ! -e /run/current-system/sw/share/nix-ld ] && [ -z "${NIX_LD:-}" ]; then
    problem="${problem:+$problem; }nix-ld is not enabled (c101 downloads CPython through uv, a dynamically linked binary that stock NixOS refuses to run)"
  fi
  [ -n "$problem" ] || return 0
  if [ "${C101L_SKIP_NIXOS_CHECK:-0}" = 1 ]; then
    warn "NixOS: $problem (continuing: C101L_SKIP_NIXOS_CHECK=1)"
    return 0
  fi
  warn "NixOS: $problem"
  {
    echo "    Add to your NixOS configuration, rebuild (sudo nixos-rebuild switch), log in again, then re-run:"
    nixos_module_snippet
    echo "    Without a rebuild: nix profile install nixpkgs#uv  (still needs nix-ld for the Python builds)."
  } >&2
  if [ "$DRY_RUN" = 1 ]; then
    info "dry-run: a real run would stop here"
    return 0
  fi
  die "NixOS is not ready for c101 (see above)"
}

# pkg_update_once: refresh package lists once per run (apt only).
PKG_UPDATED=0
pkg_update_once() {
  [ "$OS_FAMILY" = apt ] || return 0
  [ "$PKG_UPDATED" = 0 ] || return 0
  run_priv apt-get update -qq || return 1
  PKG_UPDATED=1
}

# pkg_install PKG...: install distro packages; prints guidance where the family or the host forbids it.
pkg_install() {
  [ "$#" -gt 0 ] || return 0
  case $IMMUTABLE in
    ostree)
      warn "immutable (ostree) system: not layering packages. Install $* in a toolbox/distrobox, or run: rpm-ostree install $* (then reboot)"
      return 1
      ;;
    steamos)
      warn "SteamOS has a read-only root. Install $* inside a distrobox, or run: sudo steamos-readonly disable; pacman -S $* (resets on OS updates)"
      return 1
      ;;
    nixos)
      warn "NixOS: add $* to environment.systemPackages or run: nix profile install $(printf 'nixpkgs#%s ' "$@")  (Podman: virtualisation.podman.enable + dockerCompat; see docs/SCENARIOS.md)"
      return 1
      ;;
  esac
  case $OS_FAMILY in
    apt)
      pkg_update_once || return 1
      run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
      ;;
    dnf) run_priv dnf install -y "$@" ;;
    zypper) run_priv zypper --non-interactive install "$@" ;;
    pacman) run_priv pacman -S --noconfirm --needed "$@" ;;
    apk) run_priv apk add --no-cache "$@" ;;
    *)
      warn "unknown distro family: install these yourself: $*"
      return 1
      ;;
  esac
}

# ensure_prereqs NEED_GIT: curl always, git when cloning (TLS roots come with the distro's curl package).
ensure_prereqs() {
  local need_git=$1
  local -a want=()
  have curl || want+=(curl)
  if [ "$need_git" = 1 ] && ! have git; then want+=(git); fi
  if [ "${#want[@]}" -eq 0 ]; then
    ok "curl present$([ "$need_git" = 1 ] && printf ' and git')"
    return 0
  fi
  info "installing prerequisites: ${want[*]}"
  pkg_install "${want[@]}" || warn "could not install ${want[*]}; later steps may fail"
}

ensure_uv() {
  export PATH="$HOME/.local/bin:$PATH"
  if ! have uv && [ "$IMMUTABLE" = nixos ]; then
    if [ "$DRY_RUN" = 1 ]; then
      info "dry-run: uv missing on NixOS; a real run needs nixpkgs' uv (see the NixOS check above)"
      return 0
    fi
    die "uv not found. On NixOS install it from nixpkgs (nix profile install nixpkgs#uv); astral's binary cannot run on stock NixOS"
  fi
  if have uv; then
    ok "uv $(uv --version 2>/dev/null | awk '{print $2}') at $(command -v uv)"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    info "dry-run: would download astral's uv $UV_VERSION installer to a temp file and run it (UV_NO_MODIFY_PATH=1)"
    return 0
  fi
  have curl || die "curl is required to bootstrap uv"
  local f
  f=$(tmp_file uv-installer.sh)
  info "downloading uv $UV_VERSION installer from astral.sh (kept as a file, never piped; it verifies the binary's checksum itself)"
  curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 -o "$f" "https://astral.sh/uv/$UV_VERSION/install.sh" ||
    die "could not download the uv installer (network? proxy?). Install uv yourself: https://docs.astral.sh/uv/getting-started/installation/"
  head -n 1 "$f" | grep -q '^#!' || die "downloaded uv installer does not look like a script; refusing to run it"
  UV_NO_MODIFY_PATH=1 sh "$f" || die "uv installer failed"
  have uv || die "uv installed but not on PATH ($HOME/.local/bin)"
  state_set uv_installed_by_overlay true
  ok "uv $(uv --version | awk '{print $2}')"
}

# node_state: NODE_VERSION (empty when absent), NODE_OK (1 when node and npx exist and node >= 18).
node_state() {
  NODE_VERSION=""
  NODE_OK=0
  have node || return 0
  NODE_VERSION=$(node --version 2>/dev/null | sed 's/^v//')
  if [ -n "$NODE_VERSION" ] && have npx && version_ge "$NODE_VERSION" "$NODE_MIN_MAJOR"; then NODE_OK=1; fi
  return 0
}

node_fix_hint() {
  cat <<HINT_EOF
    Playwright MCP needs Node >= $NODE_MIN_MAJOR with npx. Fix without any piped installer:
      - Ubuntu 22.04 ships Node 12: use a version manager as your user (mise: https://mise.jdx.dev,
        fnm: https://github.com/Schniz/fnm, nvm: https://github.com/nvm-sh/nvm, volta: https://volta.sh)
        and re-run this installer; the PATH block picks their shims up.
      - or enable your vendor's Node repository yourself after reading its instructions.
HINT_EOF
}

# ensure_node MODE (auto|distro|skip)
ensure_node() {
  local mode=$1 pk
  node_state
  if [ "$mode" = skip ]; then
    info "node: skipped (--node skip); current: ${NODE_VERSION:-none}"
    return 0
  fi
  if [ "$NODE_OK" = 1 ]; then
    ok "node $NODE_VERSION with npx"
    return 0
  fi
  info "node/npx missing or too old (found: ${NODE_VERSION:-none}); trying the distro package"
  pk=$(pkg_names node)
  # shellcheck disable=SC2086  # pk holds two package names on purpose
  pkg_install $pk || true
  node_state
  if [ "$NODE_OK" = 1 ]; then
    ok "node $NODE_VERSION with npx"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ] || [ "$PRIV_DEFERRED" = 1 ]; then
    info "node not installed in this run (dry-run or privileged step deferred)"
    return 0
  fi
  warn "node is ${NODE_VERSION:-missing} after the distro install."
  node_fix_hint >&2
  if [ "$mode" = distro ]; then die "--node distro: the distro's Node is unusable"; fi
  return 0
}

# ensure_gnupg: gpg is needed to verify signing-key fingerprints before they are trusted.
ensure_gnupg() {
  have gpg && return 0
  local pk
  pk=$(pkg_names gnupg)
  # shellcheck disable=SC2086
  pkg_install $pk || return 1
  [ "$DRY_RUN" = 1 ] || have gpg
}

# --- container sandbox (rootless podman by default) ---------------------------------------------------------------
# Podman refuses short image names without a TTY ("short-name resolution enforced"); c101's Dockerfile says
# FROM debian:bookworm-slim, so the build is given the same image fully qualified.
SANDBOX_BASE=docker.io/library/debian:bookworm-slim

PODMAN_WRAPPER_DEST() { printf '%s\n' "$HOME/.local/bin/c101-podman"; }

# podman_packages: sets PODMAN_REQUIRED and PODMAN_OPTIONAL for this distro family.
podman_packages() {
  case $OS_FAMILY in
    apt) PODMAN_REQUIRED="podman uidmap"; PODMAN_OPTIONAL="passt slirp4netns fuse-overlayfs" ;;
    dnf) PODMAN_REQUIRED="podman shadow-utils"; PODMAN_OPTIONAL="passt slirp4netns fuse-overlayfs" ;;
    pacman) PODMAN_REQUIRED="podman"; PODMAN_OPTIONAL="passt slirp4netns fuse-overlayfs" ;;
    zypper) PODMAN_REQUIRED="podman"; PODMAN_OPTIONAL="slirp4netns fuse-overlayfs passt" ;;
    apk) PODMAN_REQUIRED="podman shadow-uidmap"; PODMAN_OPTIONAL="slirp4netns fuse-overlayfs" ;;
    *) PODMAN_REQUIRED="podman"; PODMAN_OPTIONAL="" ;;
  esac
}

# next_free_subid FILE: first id after the highest existing range (at least 100000).
next_free_subid() {
  awk -F: -v min=100000 '{ e = $2 + $3; if (e > m) m = e } END { print (m < min ? min : m) }' "$1" 2>/dev/null || echo 100000
}

# ensure_subids: /etc/subuid and /etc/subgid ranges for this user; adds them with root unless --no-sudo.
ensure_subids() {
  local user missing=0 us gs
  user=$(id -un)
  if have getsubids && getsubids "$user" >/dev/null 2>&1; then
    ok "subuid ranges from getsubids for $user (NSS/SSSD)"
    return 0
  fi
  subid_has "$user" /etc/subuid || missing=1
  subid_has "$user" /etc/subgid || missing=1
  if [ "$missing" = 0 ]; then
    ok "subuid/subgid ranges present for $user"
    return 0
  fi
  us=$(next_free_subid /etc/subuid)
  gs=$(next_free_subid /etc/subgid)
  warn "no subuid/subgid range for $user: rootless podman cannot start containers without one"
  if have usermod; then
    run_priv usermod --add-subuids "$us-$((us + 65535))" --add-subgids "$gs-$((gs + 65535))" "$user"
  else
    run_priv sh -c 'printf "%s:%s:65536\n" "$1" "$2" >> /etc/subuid; printf "%s:%s:65536\n" "$1" "$3" >> /etc/subgid' _ "$user" "$us" "$gs"
  fi
  if [ "$PRIV_DEFERRED" = 1 ]; then
    next_step "Give $user subuid/subgid ranges, then run 'podman system migrate': sudo usermod --add-subuids $us-$((us + 65535)) --add-subgids $gs-$((gs + 65535)) $user"
  elif [ "$DRY_RUN" != 1 ]; then
    have podman && podman system migrate >/dev/null 2>&1 || true
    ok "added subuid/subgid ranges ($us.., $gs..)"
  fi
}

DELEGATE_DROPIN=/etc/systemd/system/user@.service.d/delegate.conf

# ensure_cgroup_delegation: rootless limits (--cpus, --memory, --pids-limit) need the controllers delegated to the
# user manager. Ubuntu 22.04 (systemd 249) delegates only memory and pids, so every sandbox start would fail on `cpu`.
ensure_cgroup_delegation() {
  local missing tmp staged
  missing=$(cgroup_missing_controllers)
  if [ -z "$missing" ]; then
    ok "cgroup v2: needed controllers delegated to the user manager (or not applicable)"
    return 0
  fi
  warn "cgroup controllers not delegated to your user manager: $missing. c101's sandbox passes --cpus, so rootless podman would refuse to start it."
  if [ -e "$DELEGATE_DROPIN" ]; then
    warn "$DELEGATE_DROPIN exists but the running user manager does not have the controllers: log out and in (or reboot) so user@$(id -u).service restarts"
    return 0
  fi
  tmp=$(tmp_file delegate)
  printf '[Service]\nDelegate=cpu cpuset io memory pids\n' >"$tmp"
  staged=$(stage_file "$tmp" delegate.conf)
  run_priv install -D -m 0644 -- "$staged" "$DELEGATE_DROPIN" || return 0
  run_priv systemctl daemon-reload || true
  if [ "$PRIV_DEFERRED" = 1 ]; then
    next_step "Delegate cgroup controllers (needed for the sandbox on Ubuntu 22.04): run the commands above as root, then log out and in"
  elif [ "$DRY_RUN" != 1 ]; then
    state_append system_files "$(json_str "$DELEGATE_DROPIN")"
    next_step "Log out and back in (or reboot) so your user manager restarts with cgroup delegation; then re-run the doctor"
  fi
}

# ensure_podman: install podman and what rootless needs, check ids, XDG_RUNTIME_DIR and `podman info`.
ensure_podman() {
  podman_packages
  if ! have podman; then
    info "installing podman for rootless use: $PODMAN_REQUIRED"
    # shellcheck disable=SC2086  # the lists are space separated package names
    pkg_install $PODMAN_REQUIRED || warn "podman was not installed (see above)"
  fi
  if have podman || [ "$DRY_RUN" = 1 ]; then
    # Optional helpers differ by release (passt on 24.04+, slirp4netns before, fuse-overlayfs on old kernels): best effort, one by one.
    local p
    for p in $PODMAN_OPTIONAL; do
      if have "$p"; then continue; fi
      pkg_install "$p" || info "optional helper $p is not available here; continuing without it"
    done
  fi
  if ! have podman && [ "$DRY_RUN" != 1 ]; then
    warn "podman is not available: the desktop-sandbox cannot run (docker fallback: --container-runtime docker)"
    return 0
  fi
  ensure_subids
  ensure_cgroup_delegation
  if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    warn "XDG_RUNTIME_DIR is not set (su/sudo/ssh without a login session): rootless podman needs it. Use a real login, or: export XDG_RUNTIME_DIR=/run/user/$(id -u)"
  fi
  if have loginctl && [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = no ] && [ "${IS_WSL:-0}" = 0 ]; then
    info "linger is off: over SSH the user session (and rootless podman's runtime dir) ends at logout; --enable-linger fixes that"
  fi
  if [ "$(selinux_mode)" = Enforcing ]; then
    info "SELinux is enforcing: the c101-podman wrapper disables label separation for the sandbox container (bind mounts would otherwise be denied); see docs/PITFALLS.md"
  fi
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  detect_container_runtime podman 1
  if [ "$CONTAINER_OK" = 1 ]; then
    ok "podman $(podman --version | awk '{print $3}'), rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null), storage=$(podman info --format '{{.Store.GraphDriverName}}' 2>/dev/null)"
  else
    warn "podman is installed but not usable yet: $CONTAINER_NOTE"
  fi
}

# install_podman_wrapper: ~/.local/bin/c101-podman (copy; refuses to overwrite a file that is not ours).
install_podman_wrapper() {
  local dest src=$C101L_ROOT/bin/c101-podman
  dest=$(PODMAN_WRAPPER_DEST)
  if [ -e "$dest" ] && ! grep -q 'c101-linux managed file' "$dest" 2>/dev/null; then
    warn "$dest exists and is not ours; leaving it. The sandbox will use plain podman."
    return 1
  fi
  if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
    ok "$(tilde "$dest") already current"
  else
    run mkdir -p -- "$(dirname -- "$dest")"
    run install -m 0755 -- "$src" "$dest"
    info "installed podman wrapper $(tilde "$dest") (rootless uid mapping for c101's sandbox)"
  fi
  state_append files_installed "$(json_str "$dest")"
}

# sandbox_runtime_value: what DESKTOP_SANDBOX_RUNTIME should hold for the chosen runtime ("" = leave to c101).
sandbox_runtime_value() {
  case $P_CONTAINER_RUNTIME in
    docker) printf 'docker\n' ;;
    *) if [ "${OPT_PODMAN_WRAPPER:-1}" = 1 ]; then printf '%s\n' "$(PODMAN_WRAPPER_DEST)"; else printf 'podman\n'; fi ;;
  esac
}

# setup_podman: podman, rootless prerequisites and the wrapper (everything the sandbox, the services and the shim share).
setup_podman() {
  ensure_podman
  if [ "${OPT_PODMAN_WRAPPER:-1}" = 1 ]; then install_podman_wrapper || OPT_PODMAN_WRAPPER=0; fi
}

# setup_sandbox: build c101's desktop-sandbox image with the chosen runtime.
setup_sandbox() {
  local rt=$P_CONTAINER_RUNTIME value
  if [ "$rt" = docker ]; then
    warn "--container-runtime docker: fallback only. The docker group is root-equivalent; rootless podman is the supported default"
  fi
  value=$(sandbox_runtime_value)
  # A dry run must not touch the container engine at all: `podman info` initialises rootless storage on first use.
  if [ "$DRY_RUN" = 1 ]; then
    info "dry-run: would build the sandbox image: DESKTOP_SANDBOX_RUNTIME=$value bin/desktop-sandbox --build --profile recommended --base $SANDBOX_BASE"
    return 0
  fi
  detect_container_runtime "$rt" 1
  if [ "$CONTAINER_OK" != 1 ]; then
    warn "desktop-sandbox image not built: $CONTAINER_NOTE"
    next_step "Fix the container runtime, then: DESKTOP_SANDBOX_RUNTIME=$value ~/.claude/stack/bin/desktop-sandbox --build --profile recommended --base $SANDBOX_BASE"
    return 0
  fi
  local bin=$HOME/.claude/stack/bin/desktop-sandbox
  if [ ! -x "$bin" ]; then
    warn "$bin missing (c101 --user did not run?); skipping the sandbox image"
    return 0
  fi
  info "building the sandbox image with $rt (first build takes minutes; needs network)"
  if DESKTOP_SANDBOX_RUNTIME=$value "$bin" --build --profile recommended --base "$SANDBOX_BASE"; then
    state_set sandbox_image '"c101-desktop-sandbox:2"'
    state_set sandbox_runtime "$(json_str "$rt")"
    ok "sandbox image built"
  else
    warn "sandbox image build failed; retry: DESKTOP_SANDBOX_RUNTIME=$value $bin --build --profile recommended --base $SANDBOX_BASE"
  fi
}

# --- Playwright system libraries ---------------------------------------------------------------------------------
setup_playwright_deps() {
  if [ "$OS_FAMILY" != apt ]; then
    warn "--playwright-deps: Playwright's install-deps supports Debian/Ubuntu only (this is $OS_FAMILY). Install Chromium's libraries with your package manager."
    return 0
  fi
  node_state
  if [ "$NODE_OK" != 1 ] && [ "$DRY_RUN" != 1 ]; then
    warn "--playwright-deps needs node >= $NODE_MIN_MAJOR with npx; skipped"
    return 0
  fi
  info "installing Chromium's system libraries via Playwright's own installer (apt packages, needs root)"
  run_priv env "PATH=$PATH" npx -y playwright@latest install-deps chromium || warn "playwright install-deps failed"
}
