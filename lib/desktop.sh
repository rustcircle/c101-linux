# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points
# shellcheck disable=SC2088  # a literal ~ in messages is for display
# shellcheck disable=SC2153  # KEYRING comes from detect.sh
# desktop.sh: Claude Desktop and Claude Code CLI installation (signed Anthropic repositories only), guidance for
# everything else, and the opt-in claude_desktop_config.json merge. Nothing from a community build is ever fetched.
# Needs: common.sh, detect.sh, deps.sh.

# Pinned signing-key fingerprint (Anthropic's release key; docs: code.claude.com/docs/en/desktop-linux and /setup).
CLAUDE_KEY_FPR=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE # gitleaks:allow (public signing-key fingerprint, not a secret)
DESKTOP_KEY_URL=https://downloads.claude.ai/claude-desktop/key.asc
DESKTOP_KEYRING=/usr/share/keyrings/claude-desktop-archive-keyring.asc
DESKTOP_LIST=/etc/apt/sources.list.d/claude-desktop.list
DESKTOP_REPO_URL=https://downloads.claude.ai/claude-desktop/apt/stable
CODE_KEY_URL=https://downloads.claude.ai/keys/claude-code.asc
CODE_KEYRING=/etc/apt/keyrings/claude-code.asc
CODE_LIST=/etc/apt/sources.list.d/claude-code.list
CODE_REPO_URL=https://downloads.claude.ai/claude-code/apt/stable
CLAUDE_INSTALL_URL=https://claude.ai/install.sh

desktop_installed() { have claude-desktop || { have dpkg && dpkg -s claude-desktop >/dev/null 2>&1; }; }

# key_fingerprint FILE: fingerprint of the primary key in an armored key file (isolated GNUPGHOME).
key_fingerprint() {
  local home
  home=$(mktemp -d "${C101L_TMP:-${TMPDIR:-/tmp}}/gnupg.XXXXXX")
  chmod 700 "$home"
  GNUPGHOME=$home gpg --batch --no-tty --with-colons --fingerprint --show-keys "$1" 2>/dev/null |
    awk -F: '$1=="pub"{p=1;next} p&&$1=="fpr"&&!d{print $10; d=1}'
  rm -rf -- "$home"
}

# fetch_verified_key URL OUTFILE: download, then refuse unless the primary fingerprint equals the pinned one.
fetch_verified_key() {
  local url=$1 out=$2 fpr
  curl -fsSL --retry 3 --connect-timeout 10 --max-time 60 -o "$out" "$url" || die "could not download $url"
  fpr=$(key_fingerprint "$out" | tr '[:lower:]' '[:upper:]')
  [ -n "$fpr" ] || die "could not read a fingerprint from $url (is gpg working?)"
  if [ "$fpr" != "$CLAUDE_KEY_FPR" ]; then
    die "signing key fingerprint mismatch for $url: got $fpr, expected $CLAUDE_KEY_FPR. Refusing to trust it."
  fi
  ok "key fingerprint verified: $CLAUDE_KEY_FPR"
}

# repo_registered_elsewhere URLFRAGMENT OURFILE: another apt source already lists this repository.
repo_registered_elsewhere() {
  local frag=$1 ours=$2 f
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
    [ -f "$f" ] && [ "$f" != "$ours" ] && grep -qs "$frag" "$f" && {
      printf '%s\n' "$f"
      return 0
    }
  done
  return 1
}

# install_if_different SRC DEST MODE: privileged copy only when content differs; sets WROTE=1 only when DEST really
# exists with SRC's content afterwards. SRC is first staged in the pending dir, so a deferred command (--no-sudo, no
# sudo) still refers to a file that exists after this run ends.
install_if_different() {
  WROTE=0
  local staged
  if [ -f "$2" ] && cmp -s "$1" "$2"; then return 0; fi
  staged=$(stage_file "$1" "$(basename -- "$2")")
  run_priv install -D -m "$3" -- "$staged" "$2" || return 1
  if [ "$PRIV_DEFERRED" = 1 ] || [ "$DRY_RUN" = 1 ]; then return 0; fi
  if [ -f "$2" ] && cmp -s "$1" "$2"; then
    WROTE=1
    rm -f -- "$staged"
  else
    err "$2 was not written"
    return 1
  fi
}

# apt_repo_install LABEL KEYURL KEYRING LISTFILE LISTLINE FRAGMENT PACKAGE
apt_repo_install() {
  local label=$1 keyurl=$2 keyring=$3 list=$4 line=$5 frag=$6 pkg=$7 other
  if ! have apt-get; then
    warn "$label: apt-get not found; skipping"
    return 1
  fi
  if other=$(repo_registered_elsewhere "$frag" "$list"); then
    info "$label repository already registered in $other; not adding another entry"
  elif [ "$DRY_RUN" = 1 ]; then
    info "dry-run: would download $keyurl, verify fingerprint $CLAUDE_KEY_FPR, install it as $keyring"
    info "dry-run: would write $list: $line"
  else
    ensure_gnupg || die "$label: gpg is needed to verify the signing key and could not be installed"
    have curl || die "curl is required"
    local tmpkey tmplist
    tmpkey=$(tmp_file "$label-key")
    tmplist=$(tmp_file "$label-list")
    fetch_verified_key "$keyurl" "$tmpkey"
    printf '%s\n' "$line" >"$tmplist"
    local key_new=1 list_new=1
    if [ -e "$keyring" ]; then key_new=0; fi
    if [ -e "$list" ]; then list_new=0; fi
    install_if_different "$tmpkey" "$keyring" 0644 || return 1
    if [ "$key_new" = 1 ] && [ "$WROTE" = 1 ]; then state_append apt_files "$(json_str "$keyring")"; fi
    install_if_different "$tmplist" "$list" 0644 || return 1
    if [ "$list_new" = 1 ] && [ "$WROTE" = 1 ]; then state_append apt_files "$(json_str "$list")"; fi
  fi
  run_priv apt-get update -qq || return 1
  run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" || return 1
  return 0
}

install_desktop() {
  step "Claude Desktop (official signed APT repository)"
  local support
  support=$(desktop_official_support)
  if desktop_installed; then
    ok "claude-desktop already installed ($(command -v claude-desktop || echo dpkg)); updates come from apt upgrade"
    return 0
  fi
  if [ "$support" != yes ]; then
    warn "official Claude Desktop is not available here: $support"
    desktop_guidance
    return 0
  fi
  case $OS_ID in ubuntu | debian) ;; *) warn "$OS_NAME is a Debian derivative: Anthropic lists Ubuntu 22.04+/Debian 12+ only; trying anyway" ;; esac
  [ "$IS_WSL" = 0 ] || warn "WSL: the Linux Desktop beta needs a keyring and a desktop session; the Windows app plus the CLI in WSL is usually better"
  local line="deb [arch=amd64,arm64 signed-by=$DESKTOP_KEYRING] $DESKTOP_REPO_URL stable main"
  if apt_repo_install claude-desktop "$DESKTOP_KEY_URL" "$DESKTOP_KEYRING" "$DESKTOP_LIST" "$line" "claude-desktop/apt" claude-desktop; then
    if [ "$DRY_RUN" != 1 ] && [ "$PRIV_DEFERRED" = 0 ] && desktop_installed; then
      state_set desktop_installed_by_overlay true
      ok "claude-desktop installed"
    fi
    next_step "Launch Claude Desktop from the app menu (or: claude-desktop), sign in, open the Code tab."
  else
    warn "claude-desktop was not installed (see messages above)"
  fi
}

install_cli() {
  step "Claude Code CLI"
  if have claude; then
    ok "claude CLI already present at $(command -v claude)"
    return 0
  fi
  if [ "$OS_FAMILY" = apt ] && have apt-get; then
    local line="deb [signed-by=$CODE_KEYRING] $CODE_REPO_URL stable main"
    if apt_repo_install claude-code "$CODE_KEY_URL" "$CODE_KEYRING" "$CODE_LIST" "$line" "claude-code/apt" claude-code; then
      if [ "$DRY_RUN" != 1 ] && [ "$PRIV_DEFERRED" = 0 ] && have claude; then state_set cli_installed_by_overlay '"apt"'; fi
      next_step "Package-manager installs of the CLI do not auto-update: sudo apt update && sudo apt upgrade claude-code"
    fi
    return 0
  fi
  # No exact signed-repo lines are pinned for dnf/zypper/apk/pacman in this overlay, so the vendor's own
  # installer is used: downloaded to a file and inspected, never piped into a shell.
  if [ "$OS_FAMILY" = apk ]; then
    have bash || pkg_install bash || true
  fi
  if [ "$DRY_RUN" = 1 ]; then
    info "dry-run: would download $CLAUDE_INSTALL_URL to a temp file and run it with bash (installs to ~/.local/bin)"
    return 0
  fi
  have curl || die "curl is required"
  have bash || die "bash is required by the Claude Code installer"
  local f
  f=$(tmp_file claude-install.sh)
  curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 -o "$f" "$CLAUDE_INSTALL_URL" ||
    die "could not download $CLAUDE_INSTALL_URL"
  head -n 1 "$f" | grep -q '^#!' || die "downloaded installer does not look like a script; refusing to run it"
  info "running Anthropic's installer ($(wc -c <"$f" | tr -d ' ') bytes, sha256 $(sha256sum "$f" 2>/dev/null | cut -c1-16 || echo n/a)...)"
  bash "$f" || die "the Claude Code installer failed"
  export PATH="$HOME/.local/bin:$PATH"
  state_set cli_installed_by_overlay '"official-installer"'
  if have claude; then ok "claude CLI installed at $(command -v claude)"; fi
}

desktop_guidance() {
  cat <<GUIDE_EOF
    Nothing is downloaded for these routes; they are community builds, so read them first:
GUIDE_EOF
  case $OS_FAMILY:$IMMUTABLE in
    *:ostree)
      printf '    - Immutable Fedora family: use a distrobox/toolbox with Ubuntu 24.04, install the official .deb there\n      (this installer, run inside it, does that), and export the app with distrobox-export.\n' ;;
    *:steamos)
      printf '    - SteamOS: use a distrobox with Ubuntu 24.04 (the root filesystem is read-only), then run this installer there.\n' ;;
    nix:* | *:nixos)
      printf '    - NixOS: the community flake in github.com/aaddrick/claude-desktop-debian (packages the official build), or claude-desktop-extra.\n' ;;
    pacman:*)
      printf '    - Arch: the AUR package "claude-desktop" (repackages the official build): yay -S claude-desktop\n' ;;
    dnf:* | zypper:*)
      printf '    - Fedora/RHEL/openSUSE: aaddrick/claude-desktop-debian ships an .rpm and its own dnf repo (community, unofficial);\n      claude-desktop-extra (patrickjaja) covers Fedora/RHEL too. Anthropic has not announced an rpm.\n' ;;
    apk:*)
      printf '    - Alpine/musl: no Desktop build exists (the app needs glibc). Use the Claude Code CLI (--install-cli).\n' ;;
    apt:*)
      printf '    - This Debian-family system does not meet the official requirements; a newer Ubuntu/Debian or the CLI is the fix.\n' ;;
    *)
      printf '    - Use the Claude Code CLI (--install-cli); no Desktop build is known for this system.\n' ;;
  esac
  printf '    - The Claude Code CLI works everywhere c101 does: %s\n' "$(rerun_cmd --install-cli)"
}

# desktop_prechecks: warnings only; nothing here blocks the install.
desktop_prechecks() {
  step "Desktop environment checks"
  local sup
  sup=$(desktop_official_support)
  if [ "$sup" = yes ]; then
    ok "official Desktop build supported ($ARCH, $LIBC ${GLIBC_VERSION:-})"
  else
    info "official Desktop build: $sup"
    # --install-desktop prints the same guidance itself; without it the checks do, so every Desktop-checking profile shows the options
    if [ "${OPT_INSTALL_DESKTOP:-0}" != 1 ] && ! desktop_installed; then desktop_guidance; fi
  fi
  case $KEYRING in
    none) warn "no keyring daemon is running: Desktop cannot store its sign-in. Debian/Ubuntu: sudo apt install gnome-keyring (KDE: use KWallet, do not install both), then log in again and unlock it" ;;
    unknown) info "keyring: pgrep missing, not checked" ;;
    *) ok "keyring daemon: $KEYRING (make sure it is unlocked)" ;;
  esac
  if [ "$SESSION_TYPE" = wayland ]; then
    info "Wayland session: Quick Entry needs the desktop's GlobalShortcuts portal; on X11 it works directly"
  fi
  case $HAS_KVM in
    ok) ok "/dev/kvm usable: Cowork can run" ;;
    no-access) info "/dev/kvm exists but is not accessible: Cowork needs you in the kvm group (sudo usermod -aG kvm \$USER; log in again)" ;;
    none) info "no /dev/kvm: Cowork (VM-based) will not work here; Chat and the Code tab do not need it" ;;
  esac
  if [ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ] &&
    [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ]; then
    info "AppArmor restricts unprivileged user namespaces (Ubuntu 23.10+): if Desktop or its sandboxed tools fail to start, see docs/PITFALLS.md"
  fi
  [ "$IS_WSL" = 0 ] || warn "WSL detected: prefer the headless profile and the Windows Desktop app"
  [ "$IS_CONTAINER" = 0 ] || info "container detected: Desktop needs a display and a keyring; expect to run the CLI only"
}

# desktop_chat_mcp_apply FORCE(0|1): merge config/desktop-mcp.json into claude_desktop_config.json.
desktop_chat_mcp_apply() {
  local force=${1:-0} cfg res rc=0
  cfg=${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json
  step "Claude Desktop MCP servers (opt-in)"
  warn "These servers will also be loaded by the Code tab (it reads claude_desktop_config.json too), so they start eagerly there instead of lazily per agent, and add tool definitions to every Code session."
  if dry_plan_only; then
    info "dry-run (no Python yet, plan only): would merge $C101L_ROOT/config/desktop-mcp.json into $cfg"
    return 0
  fi
  local -a args=(merge --config "$cfg" --servers "$C101L_ROOT/config/desktop-mcp.json")
  [ "$force" = 1 ] && args+=(--force)
  [ "$DRY_RUN" = 1 ] && args+=(--dry-run)
  res=$(helper desktop_config.py "${args[@]}") || rc=$?
  if [ "$rc" -eq 3 ]; then
    warn "Claude Desktop is running; it rewrites its config on exit. Quit it and re-run $(rerun_cmd --desktop-chat-mcp) (or add --force)."
    next_step "Quit Claude Desktop, then re-run: $(rerun_cmd --desktop-chat-mcp)"
    return 0
  elif [ "$rc" -ne 0 ]; then
    err "could not merge into $cfg (exit $rc); the file was left untouched"
    return 1
  fi
  info "added: $(json_field "$res" added) updated: $(json_field "$res" updated) unchanged: $(json_field "$res" unchanged) conflicts: $(json_field "$res" conflicts)"
  # updated = an entry an earlier overlay release shipped, upgraded in place (e.g. c101-playwright gains --browser chromium)
  [ -n "$(json_field "$res" backup)" ] && info "backup: $(json_field "$res" backup)"
  if [ "$DRY_RUN" != 1 ]; then
    state_set desktop_config_path "$(json_str "$cfg")"
    local names n
    find_helper_python
    # added and updated entries are both ours now; the uninstaller removes exactly these names (append dedupes)
    names=$("${C101L_PY_CMD[@]}" -I -c 'import json,sys; r=json.loads(sys.argv[1]); print("\n".join(r.get("added", []) + r.get("updated", [])))' "$res")
    while IFS= read -r n; do
      [ -n "$n" ] && state_append desktop_added_names "$(json_str "$n")"
    done <<<"$names"
  fi
  next_step "Restart Claude Desktop to load the c101-* MCP servers (Settings > Developer shows their status)."
}
