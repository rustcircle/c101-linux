# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points
# shellcheck disable=SC2088  # a literal ~ in messages is for display
# shellcheck disable=SC2016  # the generated wrapper scripts keep $HOME/$@ literal
# c101.sh: find, fetch and run the upstream c101 checkout. c101 is consumed, never copied or patched.

DEFAULT_C101_REPO=https://github.com/rustcircle/c101.git
DEFAULT_C101_REF=main

c101_clone_dir() { printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/c101-linux/c101"; }

is_c101_checkout() { [ -f "$1/install.sh" ] && [ -f "$1/stack/install.py" ] && [ -f "$1/.python-version" ]; }

# installed_c101_source: the c101 checkout an earlier install used, for --keys-only (RV1 R3): state c101_source, else
# the target of ~/.claude/stack (c101's installer links it to its checkout). Prints it and returns 0 only when it is a
# c101 checkout; reads the state only when the helper Python already exists (no download). Changes nothing.
installed_c101_source() {
  local s="" link=$HOME/.claude/stack
  if helper_python_available; then s=$(state_get c101_source 2>/dev/null) || s=""; fi
  if [ -n "$s" ] && is_c101_checkout "$s"; then
    printf '%s\n' "$s"
    return 0
  fi
  if [ -L "$link" ]; then
    s=$(readlink "$link") || s=""
    case $s in /* | '') ;; *) s=$HOME/.claude/$s ;; esac # a relative link target is relative to ~/.claude
    if [ -n "$s" ] && is_c101_checkout "$s"; then
      printf '%s\n' "$s"
      return 0
    fi
  fi
  return 1
}

# resolve_source: sets C101_SRC and C101_SRC_KIND (flag|env|installed|sibling|clone). Does not touch the disk.
# installed (--keys-only only): the checkout an earlier install used (installed_c101_source), so the fal preset comes from
# the c101 that is actually installed, not from a sibling or the default clone.
resolve_source() {
  local inst=""
  C101_SRC=""
  C101_SRC_KIND=""
  if [ -n "${OPT_SOURCE:-}" ]; then
    C101_SRC=$OPT_SOURCE
    C101_SRC_KIND=flag
  elif [ -n "${C101_SOURCE:-}" ]; then
    C101_SRC=$C101_SOURCE
    C101_SRC_KIND='env'
  elif [ "${OPT_KEYS_ONLY:-0}" = 1 ] && inst=$(installed_c101_source); then
    C101_SRC=$inst
    C101_SRC_KIND=installed
  elif is_c101_checkout "$C101L_ROOT/../c101"; then
    C101_SRC=$C101L_ROOT/../c101
    C101_SRC_KIND=sibling
  else
    C101_SRC=$(c101_clone_dir)
    C101_SRC_KIND=clone
  fi
  if [ -d "$C101_SRC" ]; then C101_SRC=$(cd -P -- "$C101_SRC" && pwd -P); fi
  return 0
}

# fetch_source: make C101_SRC a usable checkout (clone or fast-forward only for the clone kind).
fetch_source() {
  local repo=${OPT_REPO:-$DEFAULT_C101_REPO} ref=${OPT_REF:-$DEFAULT_C101_REF}
  if [ "$C101_SRC_KIND" != clone ]; then
    if [ -d "$C101_SRC" ] && is_c101_checkout "$C101_SRC"; then
      ok "c101 source: $C101_SRC ($C101_SRC_KIND)"
      [ "$DRY_RUN" = 1 ] || [ -w "$C101_SRC" ] ||
        die "$C101_SRC is not writable. c101's installer creates .venv there, restores executable bits and writes __pycache__. Use a writable copy (see docs/PITFALLS.md, 'read-only source')."
      return 0
    fi
    die "--source/\$C101_SOURCE '$C101_SRC' is not a c101 checkout (needs install.sh, stack/install.py, .python-version)"
  fi
  have git || {
    if [ "$DRY_RUN" = 1 ]; then
      info "dry-run: git is missing; it would be installed first"
    else
      die "git is required to clone c101 (install it or pass --source)"
    fi
  }
  if [ -d "$C101_SRC/.git" ]; then
    local origin
    origin=$(git -C "$C101_SRC" remote get-url origin 2>/dev/null || true)
    [ "$origin" = "$repo" ] || die "$C101_SRC has origin '$origin', not '$repo'; remove it or pass --source"
    if [ -n "$(git -C "$C101_SRC" status --porcelain 2>/dev/null)" ]; then
      warn "$C101_SRC has local changes: not updating it"
      return 0
    fi
    info "updating clone to $ref"
    run git -C "$C101_SRC" fetch --depth 1 origin "$ref"
    run git -C "$C101_SRC" checkout --quiet --detach FETCH_HEAD
  elif [ -e "$C101_SRC" ]; then
    die "$C101_SRC exists and is not a git checkout; move it away or pass --source"
  else
    info "cloning $repo ($ref) to $(tilde "$C101_SRC")"
    run mkdir -p -- "$(dirname -- "$C101_SRC")"
    run git init --quiet -- "$C101_SRC"
    run git -C "$C101_SRC" remote add origin "$repo"
    run git -C "$C101_SRC" fetch --depth 1 origin "$ref"
    run git -C "$C101_SRC" checkout --quiet --detach FETCH_HEAD
    state_set c101_cloned true
  fi
  if [ "$DRY_RUN" != 1 ]; then
    is_c101_checkout "$C101_SRC" || die "$C101_SRC is not a c101 checkout after cloning $repo at $ref"
    ok "c101 at $(git -C "$C101_SRC" rev-parse --short HEAD)"
  fi
}

# c101_exec FLAG...: run c101's installer entry point with these flags.
# Real run: `bash install.sh FLAGS` (bootstraps uv's Python 3.13 and the locked env, then python -m stack.install).
# Dry run: install.sh --dry-run still creates .venv and the uv cache (c101's docs say so), which would break the
# zero-change promise. So when the checkout already has a .venv, the same entry point install.sh execs
# (python -m stack.install --dry-run) runs from it with bytecode writing off; otherwise the command is only printed.
c101_exec() {
  export PATH="$HOME/.local/bin:$PATH"
  if [ "$DRY_RUN" = 1 ]; then
    local py=$C101_SRC/.venv/bin/python
    if [ -x "$py" ]; then
      info "c101 dry run (python -m stack.install $* --dry-run):"
      (cd -- "$C101_SRC" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$C101_SRC" "$py" -B -m stack.install "$@" --dry-run) ||
        warn "c101 dry run exited non-zero"
    else
      info "dry-run: would run: bash $C101_SRC/install.sh $*"
      info "(no .venv in the checkout yet, so c101's own --dry-run is not run: it would create one)"
    fi
    return 0
  fi
  (cd -- "$C101_SRC" && bash ./install.sh "$@")
}

# c101_run: run c101's installer with the profile's flags and record what was asked for.
c101_run() {
  local -a flags=()
  local s
  s=$(c101_flag_string)
  # shellcheck disable=SC2206  # word splitting of the flag string is intended; it holds only known flag tokens
  flags=($s)
  info "c101 installer flags: ${flags[*]-}"
  if [ "$DRY_RUN" != 1 ]; then
    # What the user's settings.json env held before c101 first ran, so the uninstaller removes only what c101 added.
    # Key NAMES only (R3): a token in that env must not be copied into state.json. A snapshot an older version wrote as an
    # object (names and values) is cut down to its names now.
    local env_prev env_names
    if env_prev=$(state_get settings_env_before 2>/dev/null); then
      case $env_prev in
        '{'*) if env_names=$(helper jsontool.py env-names - <<<"$env_prev" 2>/dev/null); then state_set settings_env_before "$env_names"; fi ;;
      esac
    else
      state_set settings_env_before "$(helper jsontool.py env-snapshot "$HOME/.claude/settings.json" 2>/dev/null || echo '[]')"
    fi
    local n
    for n in ${OPT_MCP//,/ }; do state_append c101_mcp_names "$(json_str "$n")"; done
  fi
  c101_exec ${flags[@]+"${flags[@]}"} || die "c101's install.sh failed (see its output above)"
  [ "$DRY_RUN" = 1 ] && return 0
  state_set c101_source "$(json_str "$C101_SRC")"
  state_set c101_flags "$(json_str "$s")"
  local link=$HOME/.claude/stack
  if [ -L "$link" ] && [ "$(cd -P -- "$link" && pwd -P)" = "$C101_SRC" ]; then
    ok "~/.claude/stack -> $C101_SRC"
  else
    warn "~/.claude/stack does not point at $C101_SRC (c101 skips it when a real file or directory is there)"
  fi
}

# c101_settings_check: after the run, are the stack hooks in ~/.claude/settings.json (needs --user)?
c101_hooks_present() {
  local n
  n=$(helper jsontool.py hook-count "$HOME/.claude/settings.json" "/.claude/stack/bin/stack hook" 2>/dev/null || echo 0)
  [ "${n:-0}" -gt 0 ]
}

# --- extras: command links, managed settings, refusals ---------------------------------------------------------------
BIN_MARKER="# c101-linux managed file"

# refuse_unsupported_mcp NAMES: macOS/Windows-only servers are refused with the Linux alternative, and so are the hosted fal
# servers: c101 registers them at user scope, which loads fal's ~60 tools into every session (R2; D8: the hosted fal MCP is
# installed by default but loaded only through the fal-runner agent or claude --mcp-config).
refuse_unsupported_mcp() {
  local n
  for n in ${1//,/ }; do
    case $n in
      indesign | illustrator | photoshop | after-effects | premiere-pro)
        usage_error "--mcp $n: Adobe apps do not exist on Linux (c101 only runs their setup on macOS). Use the desktop-sandbox (LibreOffice, Inkscape, GIMP/Krita in a container) for GUI work." ;;
      fal | fal-oauth)
        usage_error "--mcp $n registers the hosted fal MCP at user scope, so its ~60 tools would load in every session. Use the fal-runner agent (installed by default; it starts fal only when it is called), or load fal for one session: claude --mcp-config ~/.config/c101/fal-mcp.json" ;;
      peekaboo)
        usage_error "--mcp peekaboo drives the macOS desktop and is not available on Linux; use the desktop-sandbox server." ;;
    esac
  done
}

# link_bins: ~/.local/bin/{claude-stack,stack-sdk,c101-stack} as tiny exec wrappers (c101's launchers locate
# themselves by their own path, so a plain symlink would break claude-stack).
link_bins() {
  local pair name rel dest tmp
  run mkdir -p -- "$HOME/.local/bin"
  for pair in claude-stack:claude-stack stack-sdk:stack-sdk c101-stack:stack; do
    name=${pair%%:*}
    rel=${pair#*:}
    dest=$HOME/.local/bin/$name
    if [ -e "$dest" ] && ! grep -q "$BIN_MARKER" "$dest" 2>/dev/null; then
      warn "$(tilde "$dest") exists and is not ours; not replacing it"
      continue
    fi
    tmp=$(tmp_file bin)
    printf '#!/bin/sh\n%s\nexec "$HOME/.claude/stack/bin/%s" "$@"\n' "$BIN_MARKER" "$rel" >"$tmp"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
      ok "$(tilde "$dest") already current"
    else
      run install -m 0755 -- "$tmp" "$dest"
      info "installed $(tilde "$dest") -> ~/.claude/stack/bin/$rel"
    fi
    state_append files_installed "$(json_str "$dest")"
    rm -f -- "$tmp"
  done
}

MANAGED_SETTINGS_FILE=/etc/claude-code/managed-settings.json

# managed_settings_apply: exact model allowlist for every user and client, via c101's own command (needs root).
managed_settings_apply() {
  warn "managed settings are machine-wide: $MANAGED_SETTINGS_FILE applies to every user, the CLI and the Desktop Code tab"
  if [ ! -x "$C101_SRC/bin/stack" ] && [ "$DRY_RUN" != 1 ]; then
    warn "c101 bin/stack not found; skipping managed settings"
    return 0
  fi
  if [ "$DRY_RUN" != 1 ] && ! state_get managed_settings_preexisting >/dev/null 2>&1; then
    # keys an administrator already had in the file: the uninstaller must not strip those
    state_set managed_settings_preexisting "$(helper jsontool.py has-keys "$MANAGED_SETTINGS_FILE" availableModels availableModelsMatch enforceAvailableModels 2>/dev/null || echo '[]')"
  fi
  # PYTHONDONTWRITEBYTECODE: root must not leave root-owned __pycache__ directories in the user's checkout.
  run_priv env PYTHONDONTWRITEBYTECODE=1 "$C101_SRC/bin/stack" managed-settings --install
  if [ "$PRIV_DEFERRED" = 1 ]; then
    next_step "Apply c101's exact model allowlist (needs root): sudo env PYTHONDONTWRITEBYTECODE=1 $C101_SRC/bin/stack managed-settings --install"
  elif [ "$DRY_RUN" != 1 ]; then
    state_set managed_settings "$(json_str "$MANAGED_SETTINGS_FILE")"
  fi
}

# c101_exec_light FLAG...: like c101_exec, but without install.sh's uv bootstrap and sync when the checkout already
# has a .venv (used by the uninstaller: no network, no environment work for a removal).
c101_exec_light() {
  local py=$C101_SRC/.venv/bin/python
  if [ "$DRY_RUN" != 1 ] && [ -x "$py" ]; then
    (cd -- "$C101_SRC" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$C101_SRC" "$py" -B -m stack.install "$@")
  else
    c101_exec "$@"
  fi
}

# unlink_into SRC DIR [MAXDEPTH]: remove symlinks under DIR whose target is inside SRC (or is SRC).
unlink_into() {
  local src=$1 dir=$2 depth=${3:-1} l t
  [ -d "$dir" ] || return 0
  while IFS= read -r -d '' l; do
    t=$(readlink "$l")
    case $t in
      "$src" | "$src"/*)
        run rm -f -- "$l"
        UNLINKED=$((UNLINKED + 1))
        ;;
    esac
  done < <(find "$dir" -maxdepth "$depth" -type l -print0 2>/dev/null)
}
