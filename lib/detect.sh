# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points and the other lib files
# detect.sh: what kind of Linux is this. Pure reads; nothing here changes the system.
# Test hooks: C101_LINUX_OS_RELEASE (file), C101_LINUX_UNAME_M, C101_LINUX_LOGIN_SHELL, C101_LINUX_OSTREE_BOOTED (0|1).

osr_get() { # osr_get FILE KEY: value of KEY in an os-release file, without sourcing it
  local f=$1 key=$2 v
  [ -r "$f" ] || return 0
  v=$(sed -n "s/^${key}=//p" "$f" | head -n 1)
  v=${v#\"}
  v=${v%\"}
  v=${v#\'}
  v=${v%\'}
  printf '%s\n' "$v"
}

# family_from_ids ID ID_LIKE -> apt | dnf | pacman | zypper | apk | nix | unknown
family_from_ids() {
  local all=" $1 $2 "
  case $all in
    *" nixos "*) echo nix ;;
    *" alpine "*) echo apk ;;
    *" debian "* | *" ubuntu "*) echo apt ;;
    *" arch "* | *" archlinux "* | *" manjaro "* | *" endeavouros "*) echo pacman ;;
    *" fedora "* | *" rhel "* | *" centos "* | *" amzn "*) echo dnf ;;
    *" suse "* | *" sles "* | *opensuse*) echo zypper ;;
    *) echo unknown ;;
  esac
}

detect_os() {
  local f=${C101_LINUX_OS_RELEASE:-/etc/os-release}
  [ -r "$f" ] || f=/usr/lib/os-release
  OS_ID=$(osr_get "$f" ID)
  OS_LIKE=$(osr_get "$f" ID_LIKE)
  OS_NAME=$(osr_get "$f" PRETTY_NAME)
  OS_VERSION=$(osr_get "$f" VERSION_ID)
  OS_VARIANT=$(osr_get "$f" VARIANT_ID)
  OS_FAMILY=$(family_from_ids "$OS_ID" "$OS_LIKE")
  : "${OS_ID:=unknown}" "${OS_NAME:=unknown}"
  local m=${C101_LINUX_UNAME_M:-$(uname -m)}
  case $m in
    x86_64 | amd64) ARCH=amd64 ;;
    aarch64 | arm64) ARCH=arm64 ;;
    *) ARCH=$m ;;
  esac
  # Immutable or special distros decide where packages may go.
  IMMUTABLE=none
  local ostree=0
  case ${C101_LINUX_OSTREE_BOOTED:-} in 1) ostree=1 ;; 0) ostree=0 ;; *) [ -e /run/ostree-booted ] && ostree=1 ;; esac
  if [ "$ostree" = 1 ] || [ "$OS_VARIANT" = silverblue ] || [ "$OS_VARIANT" = kinoite ]; then
    IMMUTABLE=ostree
  elif [ "$OS_ID" = steamos ]; then
    IMMUTABLE=steamos
  elif [ "$OS_ID" = nixos ]; then
    IMMUTABLE=nixos
  fi
}

detect_libc() {
  LIBC=unknown
  GLIBC_VERSION=
  local v
  if v=$(getconf GNU_LIBC_VERSION 2>/dev/null) && [ -n "$v" ]; then
    LIBC=glibc
    GLIBC_VERSION=${v#glibc }
  else
    local l
    l=$(ldd --version 2>&1 || true)
    case $l in *musl* | *Musl*) LIBC=musl ;; esac
  fi
}

detect_runtime_env() {
  IS_WSL=0
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=1
    grep -qi 'wsl2\|microsoft-standard' /proc/version 2>/dev/null && IS_WSL=2
  fi
  IS_CONTAINER=0
  if [ -e /.dockerenv ] || [ -e /run/.containerenv ] || grep -qaE 'docker|lxc|kubepods|containerd' /proc/1/cgroup 2>/dev/null; then
    IS_CONTAINER=1
  fi
  HAS_SYSTEMD_USER=0
  if [ -d /run/systemd/system ] && [ -n "${XDG_RUNTIME_DIR:-}" ] && { [ -S "$XDG_RUNTIME_DIR/bus" ] || [ -d "$XDG_RUNTIME_DIR/systemd" ]; }; then
    HAS_SYSTEMD_USER=1
  fi
  SESSION_TYPE=${XDG_SESSION_TYPE:-}
  if [ -z "$SESSION_TYPE" ]; then
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
      SESSION_TYPE=wayland
    elif [ -n "${DISPLAY:-}" ]; then
      SESSION_TYPE=x11
    else
      SESSION_TYPE=none
    fi
  fi
  HAS_KVM=none
  if [ -e /dev/kvm ]; then
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then HAS_KVM=ok; else HAS_KVM=no-access; fi
  fi
}

detect_keyring() { # KEYRING = gnome-keyring | kwallet | none | unknown (no pgrep)
  KEYRING=none
  if ! have pgrep; then
    KEYRING=unknown
    return 0
  fi
  if pgrep -x gnome-keyring-daemon >/dev/null 2>&1; then
    KEYRING=gnome-keyring
  elif pgrep -x kwalletd5 >/dev/null 2>&1 || pgrep -x kwalletd6 >/dev/null 2>&1 || pgrep -x ksecretd >/dev/null 2>&1; then
    KEYRING=kwallet
  fi
}

detect_login_shell() { # LOGIN_SHELL = bash | zsh | fish | other
  local sh=${C101_LINUX_LOGIN_SHELL:-}
  if [ -z "$sh" ] && have getent; then
    sh=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7)
  fi
  [ -n "$sh" ] || sh=${SHELL:-/bin/sh}
  LOGIN_SHELL_PATH=$sh
  case ${sh##*/} in
    bash) LOGIN_SHELL=bash ;;
    zsh) LOGIN_SHELL=zsh ;;
    fish) LOGIN_SHELL=fish ;;
    *) LOGIN_SHELL=other ;;
  esac
}

detect_all() {
  detect_os
  detect_libc
  detect_runtime_env
  detect_keyring
  detect_login_shell
}

detect_summary() {
  printf '    distro   : %s (id=%s family=%s version=%s)\n' "$OS_NAME" "$OS_ID" "$OS_FAMILY" "${OS_VERSION:-?}"
  printf '    arch/libc: %s / %s %s\n' "$ARCH" "$LIBC" "${GLIBC_VERSION:-}"
  printf '    env      : wsl=%s container=%s systemd-user=%s session=%s immutable=%s\n' \
    "$IS_WSL" "$IS_CONTAINER" "$HAS_SYSTEMD_USER" "$SESSION_TYPE" "$IMMUTABLE"
  printf '    desktop  : keyring=%s kvm=%s login-shell=%s\n' "$KEYRING" "$HAS_KVM" "$LOGIN_SHELL"
}

# Is the Claude Desktop official .deb supported here? echo "yes" or a reason.
desktop_official_support() {
  [ "$OS_FAMILY" = apt ] || {
    echo "the official build is a .deb for Debian/Ubuntu only (this is family '$OS_FAMILY')"
    return 0
  }
  case $ARCH in amd64 | arm64) ;; *)
    echo "official builds exist for amd64 and arm64 only (this is $ARCH)"
    return 0
    ;;
  esac
  [ "$LIBC" = glibc ] || {
    echo "needs glibc (found $LIBC)"
    return 0
  }
  if [ -n "$GLIBC_VERSION" ] && ! version_ge "$GLIBC_VERSION" 2.34; then
    echo "needs libc6 >= 2.34 (found $GLIBC_VERSION)"
    return 0
  fi
  case $OS_ID in
    ubuntu) version_ge "${OS_VERSION:-0}" 22.04 || {
      echo "needs Ubuntu 22.04+ (found $OS_VERSION)"
      return 0
    } ;;
    debian) version_ge "${OS_VERSION:-0}" 12 || {
      echo "needs Debian 12+ (found $OS_VERSION)"
      return 0
    } ;;
    *) ;; # derivatives: allowed with a warning by the caller
  esac
  echo yes
}

# podman_storage_dir: where rootless podman keeps its data; it does not exist until podman was used once.
podman_storage_dir() { printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/containers/storage"; }

# detect_container_runtime [RT [PROBE]]: look at ONE runtime (default: podman). Sets CONTAINER_RT (RT when installed,
# else ""), CONTAINER_OK (1/0) and CONTAINER_NOTE (why not, and the fix). Docker is only looked at when asked for.
# `podman info` creates podman's storage on first use, so it only runs when PROBE=1 (a real install) or the storage
# already exists: the doctor and dry runs stay read-only.
detect_container_runtime() {
  local rt=${1:-podman} probe=${2:-0} out
  CONTAINER_RT=""
  CONTAINER_OK=0
  CONTAINER_NOTE="$rt is not installed"
  have "$rt" || return 0
  CONTAINER_RT=$rt
  if [ "$rt" = podman ] && [ "$probe" != 1 ] && [ ! -d "$(podman_storage_dir)" ]; then
    CONTAINER_NOTE="podman is installed but this user has never run it (no storage yet); not probed, to stay read-only"
    return 0
  fi
  if out=$("$rt" info 2>&1 >/dev/null); then
    CONTAINER_OK=1
    CONTAINER_NOTE="$rt works for this user"
    return 0
  fi
  case $out in
    *"permission denied"* | *"Permission denied"*)
      CONTAINER_NOTE="$rt socket permission denied: add yourself to the docker group (sudo usermod -aG docker \$USER; log in again; note docker-group members are root-equivalent) or use rootless podman" ;;
    *"Cannot connect"* | *"connection refused"* | *"Is the docker daemon running"*)
      CONTAINER_NOTE="$rt daemon is not running (sudo systemctl enable --now docker)" ;;
    *newuidmap* | *"no subuid"* | *subuid* | *"cannot set up namespace"*)
      CONTAINER_NOTE="rootless podman cannot map user ids: install uidmap/shadow-utils and add subuid/subgid ranges (see docs/PITFALLS.md: rootless podman)" ;;
    *)
      if [ "$rt" = podman ] && [ -z "${XDG_RUNTIME_DIR:-}" ]; then
        CONTAINER_NOTE="rootless podman needs XDG_RUNTIME_DIR (log in through a normal session; over su/ssh run: export XDG_RUNTIME_DIR=/run/user/\$(id -u))"
      else
        CONTAINER_NOTE="$rt info failed: $(printf '%s' "$out" | head -n 1)"
      fi
      ;;
  esac
  return 0
}

# subid_has USER FILE: does /etc/subuid or /etc/subgid carry a range for USER (by name or numeric uid)?
subid_has() {
  local f=$2 uid
  uid=$(id -u)
  [ -r "$f" ] && grep -Eq "^($1|$uid):[0-9]+:[0-9]+" "$f"
}

# selinux_mode: Enforcing | Permissive | Disabled | none
selinux_mode() {
  if have getenforce; then getenforce 2>/dev/null || echo none; else echo none; fi
}

# --- cgroup v2 controller delegation (rootless resource limits) --------------------------------------------------
# c101's sandbox always passes --memory, --cpus and --pids-limit. A rootless container can only use controllers that
# systemd delegated to the user's manager; Ubuntu 22.04 (systemd 249) delegates only memory and pids.
CGROUP_NEEDED="cpu memory pids"

# cgroup_missing_controllers: prints the needed controllers the user manager lacks; nothing when fine or unknown
# (cgroup v1, no user manager, unreadable). C101_LINUX_CGROUP_FILE is a test hook.
cgroup_missing_controllers() {
  local f=${C101_LINUX_CGROUP_FILE:-} uid have_c c missing=""
  uid=$(id -u)
  if [ -z "$f" ]; then
    [ -f /sys/fs/cgroup/cgroup.controllers ] || return 0
    f=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers
  fi
  [ -r "$f" ] || return 0
  have_c=" $(cat "$f" 2>/dev/null) "
  for c in $CGROUP_NEEDED; do
    case $have_c in *" $c "*) ;; *) missing="${missing:+$missing }$c" ;; esac
  done
  [ -z "$missing" ] || printf '%s\n' "$missing"
}
