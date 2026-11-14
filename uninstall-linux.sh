#!/usr/bin/env bash
# uninstall-linux.sh: remove what install-linux.sh added, tracked in ~/.local/state/c101-linux/state.json.
# It calls c101's own reversible flags, strips c101's hooks and removes ~/.claude symlinks that point into the c101
# checkout. It never touches ~/.claude/stack.env, ~/.claude/stack-data, uv, Node or distro packages.
# Overlay features are removed only as the state file recorded them: the learning backstop timer (its units through
# systemd_units, a run in progress stopped, its script through files_installed, then its stamps), the fal-runner
# agent link and fal-mcp.json (files_installed, through fal_mcp_uninstall), Desktop entries the installer added or
# updated (desktop_added_names).
# The keys file, its .bak-* backups and shared download caches are named in the closing hints, never read or changed.
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
# shellcheck source=lib/learning.sh
. "$C101L_ROOT/lib/learning.sh"
# shellcheck source=lib/falmcp.sh
. "$C101L_ROOT/lib/falmcp.sh"

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

Left in place on purpose: ~/.claude/stack.env and its .bak-* backups, ~/.claude/stack-data, uv and Python, Node,
distro packages (podman, claude-desktop), the container image, linger, Playwright's Chromium (~/.cache/ms-playwright)
and the uv/npm caches the pre-warm filled. Commands or paths for those are printed at the end.
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
  # The ~/.claude/stack link is a guess for the no-state fallback only. A state file without c101_source (a --keys-only
  # run, or an install that stopped before c101's installer) records that this overlay never installed c101.
  if [ -z "$C101_SRC" ] && [ ! -f "$sf" ] && [ -L "$HOME/.claude/stack" ]; then C101_SRC=$(readlink "$HOME/.claude/stack"); fi
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
  # desktop_added_names holds the entries the installer reported as added or as updated (an earlier release's entry
  # upgraded in place); desktop_config.py removes each only while it still equals the shipped or a legacy entry.
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
  # Every unit in systemd_units: the Podman units and the learning backstop's service and timer (timers disabled first).
  remove_podman_services
  # Without a state file nothing lists the learning backstop: check its default paths (R5).
  if [ ! -f "$sf" ]; then learn_backstop_remove_defaults; fi
  # A backstop run still in progress is stopped now that its timer is disabled, before its script and its stamps go
  # (RV1 R4); nothing happens when the service is idle or absent.
  learn_backstop_stop
  local installed f
  installed=$(state_get files_installed 2>/dev/null || true)
  if [ -n "$installed" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      # The fal-runner agent and fal-mcp.json are left to fal_mcp_uninstall below, which checks they are still ours.
      case $f in */agents/fal-runner.md | */c101/fal-mcp.json) continue ;; esac
      # Marked files only: c101-podman, ~/.local/bin/c101-learn-backstop and the other commands the installer wrote.
      if [ -f "$f" ] && grep -q 'c101-linux managed file' "$f" 2>/dev/null; then
        run rm -f -- "$f"
        info "removed $(tilde "$f")"
      fi
    done < <(helper_list "$installed")
  fi
  # The backstop's per-transcript stamps live in the state directory; they go before the state file and directory do.
  learn_timer_purge_stamps
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

  # --- fal MCP: the fal-runner agent link and the whole-session config, as files_installed recorded them -----------
  # Needs the state file (files_installed, overlay_root, fal_mcp_config_sha256); without one it checks the default paths.
  fal_mcp_uninstall

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
  elif [ -z "$C101_SRC" ] && [ -f "$sf" ]; then
    info "the state file records no c101 install by this overlay (a --keys-only run, for one): c101's settings, hooks and links are left as they are"
  elif [ -z "$C101_SRC" ]; then
    warn "cannot tell where c101 is installed (no state and no ~/.claude/stack link): nothing to undo there"
  else
    step "c101 settings, hooks and links ($C101_SRC)"
    local -a un=()
    case " $c101_flags " in *" --main-agent "*) un+=(--no-main-agent) ;; esac
    case " $c101_flags " in *" --enforce-models "*) un+=(--no-enforce-models) ;; esac
    if [ -n "$c101_flags" ]; then un+=(--no-python-default); fi
    local undo_hint="remove the 'agent' key, availableModels, enforceAvailableModels, UV_PYTHON, UV_PYTHON_PREFERENCE"
    if [ "${#un[@]}" -gt 0 ] && [ -d "$C101_SRC" ] && is_c101_checkout "$C101_SRC" && [ ! -e "$HOME/.claude/stack.env" ]; then
      # Read-only existence test: c101's installer (any flag) first creates ~/.claude/stack.env from its template when
      # it is missing, and the keys file is user data this uninstaller never writes. So the reversal is the user's.
      warn "c101 reversal (${un[*]}) skipped: c101's installer would create ~/.claude/stack.env from its template"
      next_step "Undo c101's settings by hand in ~/.claude/settings.json: $undo_hint (or run c101's installer with ${un[*]} yourself; it recreates ~/.claude/stack.env from the template)"
    elif [ "${#un[@]}" -gt 0 ] && [ -d "$C101_SRC" ] && is_c101_checkout "$C101_SRC"; then
      info "c101 reversal flags: ${un[*]}"
      c101_exec_light "${un[@]}" || warn "c101 reversal exited non-zero; check ~/.claude/settings.json"
    elif [ "${#un[@]}" -gt 0 ]; then
      warn "c101 checkout $C101_SRC is gone; undo by hand in ~/.claude/settings.json: $undo_hint"
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
      # BEFORE goes in on stdin ("-"): an older state may still hold an object with values, which must not reach a command line.
      res=$(helper jsontool.py strip-env "$HOME/.claude/settings.json" "$C101_SRC/.claude/settings.json" - $(_helper_dry_flag) <<<"$before") &&
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
  # Manual-cleanup hints, paths only: the keys file and its backups are user data, the caches are shared with other
  # tools, so nothing below is read, written or removed by this script.
  next_step "Kept, your data: $(tilde "${CLAUDE_STACK_ENV:-$HOME/.claude/stack.env}") (API keys) and ~/.claude/stack-data (learning data)"
  next_step "Kept: the keys file's backups from --keys-overwrite, $(tilde "${CLAUDE_STACK_ENV:-$HOME/.claude/stack.env}").bak-<UTC time> (beside its target if it is a symlink); they hold key values, so delete the ones you no longer need by hand"
  next_step "Kept: Playwright's Chromium for Playwright MCP, downloaded at user level into $(tilde "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}") (default ~/.cache/ms-playwright); remove it by hand when no other Playwright tool needs it"
  next_step "Kept: caches the pre-warm filled, shared with other tools: uv's (uv cache clean mcp-server-fetch neural-memory markitdown-mcp) and npm's (npx packages in ~/.npm/_npx; npm cache clean --force empties the download cache)"
  next_step "Kept: uv and Python, Node and distro packages"
  print_manual_and_next
  if [ "$DRY_RUN" = 1 ]; then log ""; log "dry run complete: nothing was removed."; fi
}

main "$@"
