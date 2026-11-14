# shellcheck shell=bash
# learning.sh: the opt-in systemd user timer that backstops c101's learning loop on Linux.
# c101 records a session's statistics (stack-data/history.jsonl) and applies learned maxTurns/effort values only in
# `stack learn run`, started by the SessionEnd hook alone; a Desktop quit, logout or crash skips SessionEnd. The timer
# runs bin/c101-learn-backstop every 30 minutes, which re-runs `learn run --transcript P` for transcripts that missed it.
# Needs: common.sh (run, step, info, ok, warn, die, next_step, state_*, json_str, tilde, dry_plan_only, DRY_RUN),
# services.sh (install_unit, detect_init, user_unit_dir, sd_user, SYSTEMD_USER_OK); c101.sh (BIN_MARKER) when sourced.
#
# Contract for install-linux.sh (I1)
#   Source after common.sh, c101.sh and services.sh. Call `learn_timer_install [ENABLE]` after the c101 installer ran
#   (ENABLE defaults to $LEARN_TIMER; profile key LEARN_TIMER, flags --learn-timer / --no-learn-timer).
#   - ENABLE != 1: changes nothing on the system. Records learn_timer=false unless an earlier run installed the timer
#     (then it stays installed and recorded true; turning it off is `systemctl --user disable --now
#     c101-learn-backstop.timer` or the uninstaller).
#   - ENABLE == 1 without a reachable systemd user manager: warns, adds a cron next step, records learn_timer=false.
#   - ENABLE == 1: copies bin/c101-learn-backstop to ~/.local/bin/c101-learn-backstop (refuses a file there that lacks
#     the '# c101-linux managed file' marker), installs both units with install_unit, runs daemon-reload, enables the
#     timer with `systemctl --user enable --now c101-learn-backstop.timer` (as the Podman timers are), records true.
#   Dry run: prints the plan through run/install_unit; state is never written (state_* are no-ops under DRY_RUN=1).
#   Returns 0 in every case above (a failure only warns, so the install continues); it dies only when the overlay's
#   own source files are missing.
#   State keys written: learn_timer (JSON true/false), systemd_units (+ the two unit paths, via install_unit),
#   files_installed (+ ~/.local/bin/c101-learn-backstop). No key names or values are written anywhere.
#
# Contract for uninstall-linux.sh (U1)
#   Units and the script need nothing new: remove_podman_services disables every *.timer listed in systemd_units and
#   removes the marked unit files, and the files_installed loop removes the marked script. Call `learn_backstop_stop`
#   after the timer is disabled (a run in progress must not lose its stamps), then
#   `learn_timer_purge_stamps` before the state directory is removed: the backstop keeps per-transcript stamps (its own
#   files only) in ${XDG_STATE_HOME:-~/.local/state}/c101-linux/learn-backstop, which would keep that directory alive.
#   stack-data (learn.log, obs, history.jsonl) belongs to c101 and is kept.
#
# For the doctor (DR1): learn_timer true -> both unit files in $(user_unit_dir) start with UNIT_MARKER,
#   ~/.local/bin/c101-learn-backstop is executable and carries the file marker, `systemctl --user is-enabled
#   c101-learn-backstop.timer` says enabled; `c101-learn-backstop --dry-run` is read-only and lists pending transcripts.

LEARN_UNITS="c101-learn-backstop.service c101-learn-backstop.timer"
LEARN_TIMER_UNIT=c101-learn-backstop.timer
LEARN_SERVICE_UNIT=c101-learn-backstop.service
LEARN_FILE_MARKER=${BIN_MARKER:-"# c101-linux managed file"}

learn_backstop_dest() { printf '%s\n' "$HOME/.local/bin/c101-learn-backstop"; }
learn_backstop_stamp_dir() { printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/c101-linux/learn-backstop"; }

# learn_backstop_install: copy the backstop script to ~/.local/bin (a copy, like c101-podman: it keeps working when the
# overlay checkout moves). Returns 1, after a warning, when the destination is not ours or the copy fails.
learn_backstop_install() {
  local src=$C101L_ROOT/bin/c101-learn-backstop dest
  dest=$(learn_backstop_dest)
  [ -r "$src" ] || die "missing $src"
  grep -q "$LEARN_FILE_MARKER" "$src" || die "$src lacks the '$LEARN_FILE_MARKER' marker"
  if [ -e "$dest" ] && ! grep -q "$LEARN_FILE_MARKER" "$dest" 2>/dev/null; then
    warn "$(tilde "$dest") exists and is not ours; not replacing it, so the learning backstop timer is not installed"
    return 1
  fi
  if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
    ok "$(tilde "$dest") already current"
  else
    if ! run mkdir -p -- "$(dirname -- "$dest")" || ! run install -m 0755 -- "$src" "$dest"; then
      warn "could not install $(tilde "$dest"); the learning backstop timer is not installed"
      return 1
    fi
    info "installed $(tilde "$dest") (learning backstop)"
  fi
  state_append files_installed "$(json_str "$dest")"
}

# learn_timer_install [ENABLE]: see the contract above.
learn_timer_install() {
  local want=${1:-${LEARN_TIMER:-0}} prev="" u
  if [ "$want" != 1 ]; then
    if ! dry_plan_only; then prev=$(state_get learn_timer 2>/dev/null || true); fi
    if [ "$prev" = true ]; then
      info "learning backstop timer: kept as installed earlier (turn it off: systemctl --user disable --now $LEARN_TIMER_UNIT)"
    else
      state_set learn_timer false
    fi
    return 0
  fi
  step "Learning backstop timer ($LEARN_TIMER_UNIT)"
  detect_init
  if [ "$SYSTEMD_USER_OK" != 1 ]; then
    warn "no systemd user manager reachable: the learning backstop timer was not installed (sessions that end without SessionEnd keep losing their statistics)"
    next_step "Without systemd user units, run the learning backstop from cron instead: */30 * * * * $C101L_ROOT/bin/c101-learn-backstop (it needs GNU find: BusyBox find, e.g. Alpine's, lacks -printf, so install GNU findutils there: apk add findutils)"
    state_set learn_timer false
    return 0
  fi
  if ! learn_backstop_install; then
    state_set learn_timer false
    return 0
  fi
  run mkdir -p -- "$(user_unit_dir)"
  for u in $LEARN_UNITS; do install_unit "$u"; done
  sd_user daemon-reload || warn "systemctl --user daemon-reload failed"
  if sd_user enable --now "$LEARN_TIMER_UNIT"; then
    state_set learn_timer true
    ok "every 30 minutes: learn run for sessions that ended without SessionEnd ($LEARN_TIMER_UNIT)"
  else
    warn "could not enable $LEARN_TIMER_UNIT; check: systemctl --user status $LEARN_TIMER_UNIT"
    state_set learn_timer false
  fi
}

# learn_backstop_stop: for the uninstaller, after the timer was disabled and BEFORE learn_timer_purge_stamps: a backstop
# run still in progress would otherwise lose its stamp directory mid-run (its mv fails, the unit is left failed). Stops
# c101-learn-backstop.service only while it is active; nothing to do without systemctl, without a user manager, or when
# the unit is absent or idle. Dry run: the stop is only printed (run). Always returns 0.
learn_backstop_stop() {
  local svc=$LEARN_SERVICE_UNIT
  have systemctl || return 0
  systemctl --user is-active --quiet "$svc" 2>/dev/null || return 0
  if run systemctl --user stop "$svc"; then
    if [ "$DRY_RUN" != 1 ]; then info "stopped $svc (a backstop run was in progress)"; fi
  else
    warn "could not stop $svc; check: systemctl --user status $svc"
  fi
  return 0
}

# learn_backstop_remove_defaults: for the uninstaller when there is NO state file (R5), the way fal_mcp_uninstall checks
# its default paths: disables the timer, stops a run in progress, then removes the two units (only while they carry
# UNIT_MARKER) and ~/.local/bin/c101-learn-backstop (only while it carries the file marker) at their default paths. A file
# that is not ours stays. Dry run: only printed. Always returns 0.
learn_backstop_remove_defaults() {
  local d u f
  d=$(user_unit_dir)
  f=$(learn_backstop_dest)
  [ -e "$d/$LEARN_TIMER_UNIT" ] || [ -e "$d/$LEARN_SERVICE_UNIT" ] || [ -e "$f" ] || return 0
  detect_init
  if [ "$SYSTEMD_USER_OK" = 1 ] && [ -e "$d/$LEARN_TIMER_UNIT" ] && grep -q "$UNIT_MARKER" "$d/$LEARN_TIMER_UNIT" 2>/dev/null; then
    run systemctl --user disable --now "$LEARN_TIMER_UNIT" 2>/dev/null || true
  fi
  learn_backstop_stop
  for u in $LEARN_UNITS; do
    [ -e "$d/$u" ] || continue
    # shellcheck disable=SC2046
    if helper managed_block.py remove-file --file "$d/$u" --marker "$UNIT_MARKER" $(_helper_dry_flag) >/dev/null; then
      info "removed $(tilde "$d/$u")"
    fi
  done
  if [ "$SYSTEMD_USER_OK" = 1 ]; then run systemctl --user daemon-reload || true; fi
  if [ -f "$f" ] && grep -q "$LEARN_FILE_MARKER" "$f" 2>/dev/null; then
    run rm -f -- "$f"
    info "removed $(tilde "$f") (learning backstop)"
  fi
  return 0
}

# learn_timer_purge_stamps: for the uninstaller; removes the backstop's stamp directory (files only it writes).
learn_timer_purge_stamps() {
  local d
  d=$(learn_backstop_stamp_dir)
  [ -d "$d" ] || return 0
  run rm -rf -- "${d:?}"
  info "removed $(tilde "$d") (learning backstop stamps)"
}
