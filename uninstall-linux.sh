#!/usr/bin/env bash
# uninstall-linux.sh: remove what install-linux.sh added, tracked in ~/.local/state/c101-linux/state.json.
# It calls c101's own reversible flags, strips c101's hooks and removes ~/.claude symlinks that point into the c101
# checkout. It never touches ~/.claude/stack.env, ~/.claude/stack-data, uv, Node or distro packages.
# Usage: uninstall-linux.sh [--dry-run] [--yes] [--purge-apt-repo] [--purge-clone] [--keep-c101] [--force] [--no-sudo]
if [ -z "${BASH_VERSION:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "uninstall-linux.sh needs bash >= 4" >&2
  exit 2
fi
set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

C101L_ROOT=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly C101L_ROOT
PROG=${0##*/}

# shellcheck source=lib/common.sh
. "$C101L_ROOT/lib/common.sh"
# shellcheck source=lib/detect.sh
. "$C101L_ROOT/lib/detect.sh"
# shellcheck source=lib/c101.sh
. "$C101L_ROOT/lib/c101.sh"
# shellcheck source=lib/pathprofile.sh
. "$C101L_ROOT/lib/pathprofile.sh"
# shellcheck source=lib/services.sh
. "$C101L_ROOT/lib/services.sh"

usage() {
  cat <<USAGE_EOF
$PROG: remove what install-linux.sh added

  -n, --dry-run        show what would be removed; change nothing
  -y, --yes            do not ask before deleting a clone (--purge-clone)
  --purge-apt-repo     also remove the apt sources and keyrings this overlay wrote (packages stay; commands are printed)
  --purge-clone        also delete the c101 clone this overlay made (only ~/.local/share/c101-linux/c101)
  --keep-c101          leave c101 itself alone: no c101 flags, no hook stripping, no ~/.claude symlink removal
  --force              remove Desktop config entries even while Claude Desktop is running
  --no-sudo            print privileged commands instead of running them
  --allow-root         run as root anyway (containers/CI)
  -h, --help

Left in place on purpose: ~/.claude/stack.env, ~/.claude/stack-data, uv and Python, Node, distro packages
(podman, claude-desktop), the container image, linger. Commands for those are printed at the end.
Exit codes: 0 ok, 1 failure, 2 usage, 3 refused.
USAGE_EOF
}

OPT_PURGE_APT=0 OPT_PURGE_CLONE=0 OPT_KEEP_C101=0 OPT_FORCE=0 OPT_ALLOW_ROOT=0
UNLINKED=0

parse_args() {
  while [ $# -gt 0 ]; do
    case $1 in
      -n | --dry-run) DRY_RUN=1 ;;
      -y | --yes) ASSUME_YES=1 ;;
      --purge-apt-repo) OPT_PURGE_APT=1 ;;
      --purge-clone) OPT_PURGE_CLONE=1 ;;
      --keep-c101) OPT_KEEP_C101=1 ;;
      --force) OPT_FORCE=1 ;;
      --no-sudo) NO_SUDO=1 ;;
      --allow-root) OPT_ALLOW_ROOT=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) usage_error "unknown option: $1" ;;
    esac
    shift
  done
}

cleanup() {
  if [ -n "${C101L_TMP:-}" ]; then rm -rf -- "$C101L_TMP"; fi
  return 0
}

main() {
  parse_args "$@"
  if [ "$(uname -s)" != Linux ]; then
    err "this uninstaller is Linux only (found $(uname -s))"
    exit 3
  fi
  if [ "$(effective_uid)" -eq 0 ] && [ "$OPT_ALLOW_ROOT" != 1 ]; then
    err "refusing to run as root: the overlay lives in your home directory. Pass --allow-root in a container."
    exit 3
  fi
  C101L_TMP=$(mktemp -d "${TMPDIR:-/tmp}/c101-linux-un.XXXXXX")
  trap cleanup EXIT
  detect_login_shell

  if [ "$DRY_RUN" = 1 ]; then step "c101-linux uninstall (dry run)"; else step "c101-linux uninstall"; fi
  local sf
  sf=$(state_file)
  if [ -f "$sf" ]; then
    info "state: $(tilde "$sf")"
  else
    warn "no state file at $(tilde "$sf"): falling back to what can be recognised (marker blocks, ~/.claude links)"
  fi

  # --- where c101 lives -------------------------------------------------------------------------------------------
  C101_SRC=$(state_get c101_source 2>/dev/null || true)
  if [ -z "$C101_SRC" ] && [ -L "$HOME/.claude/stack" ]; then C101_SRC=$(readlink "$HOME/.claude/stack"); fi
  local c101_flags
  c101_flags=$(state_get c101_flags 2>/dev/null || true)

  # --- PATH block, environment.d ----------------------------------------------------------------------------------
  step "Shell profile and environment.d"
  if [ ! -f "$sf" ]; then
    local f
    for f in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile" "${ZDOTDIR:-$HOME}/.zprofile" \
      "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/c101-linux.fish"; do
      # shellcheck disable=SC2046
      if [ -f "$f" ] && helper managed_block.py remove --file "$f" $(_helper_dry_flag) >/dev/null 2>&1; then info "checked $(tilde "$f")"; fi
    done
    # shellcheck disable=SC2046
    helper managed_block.py remove-file --file "$(envd_file)" --marker "$ENVD_MARKER" $(_helper_dry_flag) >/dev/null || true
  else
    path_remove
  fi

  # --- Desktop config entries -------------------------------------------------------------------------------------
  local names cfg
  names=$(state_get desktop_added_names 2>/dev/null || true)
  cfg=$(state_get desktop_config_path 2>/dev/null || true)
  if [ -n "$names" ] && [ -n "$cfg" ]; then
    step "Claude Desktop config entries"
    local csv
    csv=$(helper_list "$names" | paste -sd, -)
    local -a dargs=(remove --config "$cfg" --servers "$C101L_ROOT/config/desktop-mcp.json" --names "$csv")
    if [ "$OPT_FORCE" = 1 ]; then dargs+=(--force); fi
    if [ "$DRY_RUN" = 1 ]; then dargs+=(--dry-run); fi
    local res rc=0
    res=$(helper desktop_config.py "${dargs[@]}") || rc=$?
    if [ "$rc" -eq 3 ]; then
      warn "Claude Desktop is running; quit it and re-run (or --force). Entries left: $csv"
    elif [ "$rc" -ne 0 ]; then
      warn "could not edit $cfg (exit $rc); remove the c101-* entries by hand"
    else
      info "removed: $(json_field "$res" removed) (modified or absent entries are never touched)"
    fi
  fi

  # --- services, wrapper, links -----------------------------------------------------------------------------------
  step "Podman services and installed commands"
  remove_podman_services
  local installed f
  installed=$(state_get files_installed 2>/dev/null || true)
  if [ -n "$installed" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      if [ -f "$f" ] && grep -q 'c101-linux managed file' "$f" 2>/dev/null; then
        run rm -f -- "$f"
        info "removed $(tilde "$f")"
      fi
    done < <(helper_list "$installed")
  fi
  local sysf
  sysf=$(state_get system_files 2>/dev/null || true)
  if [ -n "$sysf" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      run_priv rm -f -- "$f"
      info "removed $f (cgroup delegation drop-in written by the installer)"
    done < <(helper_list "$sysf")
    run_priv systemctl daemon-reload || true
    next_step "Log out and in (or reboot) so the user manager drops the cgroup delegation drop-in"
  fi
  local lg
  lg=$(state_get linger_enabled_by_overlay 2>/dev/null || true)
  if [ "$lg" = true ]; then next_step "Linger was enabled by the installer; to turn it off: sudo loginctl disable-linger $(id -un)"; fi

  # --- managed settings -------------------------------------------------------------------------------------------
  local ms
  ms=$(state_get managed_settings 2>/dev/null || true)
  if [ -n "$ms" ]; then
    step "Managed settings"
    find_helper_python
    # Only keys the installer's merge introduced: an administrator's own earlier values stay.
    local pre k
    local -a mkeys=()
    pre=$(state_get managed_settings_preexisting 2>/dev/null || echo '[]')
    for k in availableModels availableModelsMatch enforceAvailableModels; do
      case $pre in *"\"$k\""*) ;; *) mkeys+=("$k") ;; esac
    done
    if [ "${#mkeys[@]}" -gt 0 ]; then
      # shellcheck disable=SC2046
      run_priv "${C101L_PY_CMD[@]}" -I "$C101L_ROOT/lib/jsontool.py" strip-keys "$ms" "${mkeys[@]}" $(_helper_dry_flag)
    else
      info "managed settings keys existed before the installer; left in place"
    fi
  fi

  # --- c101 itself ------------------------------------------------------------------------------------------------
  if [ "$OPT_KEEP_C101" = 1 ]; then
    info "--keep-c101: c101's settings, hooks and links are left as they are"
  elif [ -z "$C101_SRC" ]; then
    warn "cannot tell where c101 is installed (no state and no ~/.claude/stack link): nothing to undo there"
  else
    step "c101 settings, hooks and links ($C101_SRC)"
    local -a un=()
    case " $c101_flags " in *" --main-agent "*) un+=(--no-main-agent) ;; esac
    case " $c101_flags " in *" --enforce-models "*) un+=(--no-enforce-models) ;; esac
    if [ -n "$c101_flags" ]; then un+=(--no-python-default); fi
    if [ "${#un[@]}" -gt 0 ] && [ -d "$C101_SRC" ] && is_c101_checkout "$C101_SRC"; then
      info "c101 reversal flags: ${un[*]}"
      c101_exec_light "${un[@]}" || warn "c101 reversal exited non-zero; check ~/.claude/settings.json"
    elif [ "${#un[@]}" -gt 0 ]; then
      warn "c101 checkout $C101_SRC is gone; undo by hand in ~/.claude/settings.json: remove the 'agent' key, availableModels, enforceAvailableModels, UV_PYTHON, UV_PYTHON_PREFERENCE"
    fi
    # Hooks are not removed by any c101 flag; left behind they would fail on every session once the link is gone.
    if [ -f "$HOME/.claude/settings.json" ]; then
      # shellcheck disable=SC2046
      res=$(helper jsontool.py strip-hooks "$HOME/.claude/settings.json" "/.claude/stack/bin/stack hook" $(_helper_dry_flag)) &&
        info "stack hooks removed from settings.json: $(json_field "$res" removed)"
    fi
    # c101's env merge (limits, depth, UV_PYTHON handled by --no-python-default): only keys it added and still holds.
    local before
    before=$(state_get settings_env_before 2>/dev/null || true)
    if [ -n "$before" ] && [ -f "$HOME/.claude/settings.json" ] && [ -f "$C101_SRC/.claude/settings.json" ]; then
      # shellcheck disable=SC2046
      res=$(helper jsontool.py strip-env "$HOME/.claude/settings.json" "$C101_SRC/.claude/settings.json" "$before" $(_helper_dry_flag)) &&
        info "c101 env keys removed from settings.json: $(json_field "$res" removed)"
    elif [ -z "$before" ]; then
      info "no record of settings.json env before c101 ran: its env keys (MAX_MCP_OUTPUT_TOKENS, CLAUDE_CODE_MAX_*) are left in place"
    fi
    # Optional servers c101 registered at user scope through the claude CLI (--mcp).
    local mcp n
    mcp=$(state_get c101_mcp_names 2>/dev/null || true)
    if [ -n "$mcp" ]; then
      while IFS= read -r n; do
        [ -n "$n" ] || continue
        if have claude; then
          run claude mcp remove --scope user "$n" || warn "could not remove MCP server $n"
        else
          next_step "Remove the MCP server c101 registered: claude mcp remove --scope user $n"
        fi
      done < <(helper_list "$mcp")
    fi
    UNLINKED=0
    unlink_into "$C101_SRC" "$HOME/.claude/agents/stack" 6
    unlink_into "$C101_SRC" "$HOME/.claude/skills" 1
    unlink_into "$C101_SRC" "$HOME/.claude/workflows" 1
    unlink_into "$C101_SRC" "$HOME/.claude" 1
    info "removed $UNLINKED symlink(s) into the c101 checkout"
    if [ "$DRY_RUN" != 1 ] && [ -d "$HOME/.claude/agents/stack" ]; then
      find "$HOME/.claude/agents/stack" -depth -type d -empty -delete 2>/dev/null || true
    fi
  fi

  # --- apt repositories -------------------------------------------------------------------------------------------
  local af
  af=$(state_get apt_files 2>/dev/null || true)
  if [ "$OPT_PURGE_APT" = 1 ] && [ -n "$af" ]; then
    step "APT repositories"
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      run_priv rm -f -- "$f"
    done < <(helper_list "$af")
    info "packages stay installed: sudo apt remove claude-desktop claude-code  (then: sudo apt update)"
  elif [ -n "$af" ]; then
    next_step "APT sources written by the overlay remain; remove them with --purge-apt-repo (packages: sudo apt remove claude-desktop claude-code)"
  fi

  # --- clone ------------------------------------------------------------------------------------------------------
  local clone
  clone=$(state_get c101_clone 2>/dev/null || true)
  if [ "$OPT_PURGE_CLONE" = 1 ] && [ -n "$clone" ] && [ "$clone" = "$(c101_clone_dir)" ]; then
    if confirm "Delete the c101 clone at $clone?"; then
      run rm -rf -- "${clone:?}"
      info "deleted $clone"
    else
      info "kept $clone"
    fi
  elif [ -n "$clone" ]; then
    next_step "The c101 clone remains at $clone (--purge-clone deletes it)"
  fi

  local img
  img=$(state_get sandbox_image 2>/dev/null || true)
  if [ -n "$img" ]; then next_step "The sandbox image remains: podman rmi $img"; fi

  # Leftovers of earlier deferred root commands (staged payloads, pending-root-commands.sh): list them, then remove the
  # directory (it holds only files this overlay wrote). Root commands this uninstall defers are written after this.
  local pd
  pd=$(pending_dir)
  if [ -d "$pd" ] && [ -n "$(find "$pd" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    info "removing leftover pending files from earlier runs: $(find "$pd" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort | paste -sd' ' -)"
    run rm -rf -- "${pd:?}"
  fi
  if [ "$DRY_RUN" != 1 ] && [ -f "$sf" ]; then
    rm -f -- "$sf"
    rmdir "$(dirname -- "$sf")" 2>/dev/null || true
    info "state file removed"
  fi
  next_step "Kept: ~/.claude/stack.env and ~/.claude/stack-data (your keys and learning data), uv, Node and distro packages"
  print_manual_and_next
  if [ "$DRY_RUN" = 1 ]; then log ""; log "dry run complete: nothing was removed."; fi
}

main "$@"
