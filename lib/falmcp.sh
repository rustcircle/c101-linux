# shellcheck shell=bash
# falmcp.sh: the hosted fal MCP server (https://mcp.fal.ai/mcp), "installed by default, not loaded by default" (D8).
# Two pieces, both shipped in this overlay and both optional to use:
#   - agents/fal-runner.md: an overlay agent that carries the server inline (frontmatter mcpServers, headersHelper ->
#     c101's bin/mcp-headers fal, which reads FAL_KEY from ~/.claude/stack.env at run time). The server connects only
#     while that agent runs. Linked to ~/.claude/agents/fal-runner.md.
#   - config/fal-mcp.json: the same server for a whole session, `claude --mcp-config <file>`. Copied to
#     ${XDG_CONFIG_HOME:-~/.config}/c101/fal-mcp.json (mode 0644).
# Nothing here registers a server with the claude CLI at any scope, edits c101's optional-server selection or
# ~/.claude/settings.json, or reads or writes a key value: the files hold no secret, the key stays in stack.env.
# Needs: common.sh (run, step, info, ok, warn, die, have, state_*, json_str, tilde, dry_plan_only, find_helper_python,
# C101L_PY_CMD, DRY_RUN, C101L_ROOT); pathprofile.sh (helper_list) for fal_mcp_uninstall.
#
# Link or copy (decided from the code): the agent is a symlink into the overlay checkout, as c101 links its own agents
# into its checkout. C101L_ROOT is always the physical directory of the entry script (cd -P; pwd -P), never a temp dir
# (only C101L_TMP is, and it holds run scratch files); the overlay is meant to stay on disk (the installer's closing
# step runs $C101L_ROOT/bin/c101-linux-doctor, state records overlay_root, and the uninstaller reads config/ from it).
# A link also picks up agent updates on `git pull` without a re-install. The config is a copy because it is a user-facing
# path handed to `claude --mcp-config` that must keep working, mode 0644, if the checkout moves.
#
# Contract for install-linux.sh (I1)
#   Source after common.sh. Call `fal_mcp_install [ENABLE]` at main() top level after c101_run (the
#   agent's headersHelper falls back to ~/.claude/stack/bin/mcp-headers, which c101 links; ~/.claude/agents is created
#   if missing). ENABLE defaults to $FAL_MCP, then $P_FAL_MCP, then 0; pass the effective value explicitly (profile key
#   FAL_MCP, flags --fal-mcp / --no-fal-mcp).
#   - ENABLE != 1: changes nothing on disk. Records fal_mcp=false, unless an earlier run installed the agent and the
#     link is still ours: then it stays and fal_mcp stays true (the info line says how to remove it).
#   - ENABLE == 1: links the agent and copies the config. A path that exists and is not ours (a link elsewhere, a file
#     without the 'c101-linux managed file' marker, a config that differs from the shipped one and from what this
#     overlay last installed) is left alone with a warning; the two pieces are independent. Prints one hint line naming
#     the agent and `claude --mcp-config <file>` (whichever is in place). Records fal_mcp=true when the agent link is in
#     place after the run, else false (the config alone does not make it true).
#   Dry run: prints the plan through run (dry-run: lines); state is never written (state_* are no-ops under DRY_RUN=1).
#   Returns 0 in every case above (refusals only warn); dies only when agents/fal-runner.md or config/fal-mcp.json is
#   missing from the overlay (a packaging error).
#   State keys written: fal_mcp (JSON true/false); files_installed (+ ~/.claude/agents/fal-runner.md, + the config path,
#   each only when it is ours after the run); fal_mcp_config_sha256 (sha256 of the config as installed, so an older
#   copy of ours is still recognised after the shipped file changes). No key names or values are written anywhere.
#
# Contract for uninstall-linux.sh (U1)
#   Call `fal_mcp_uninstall` anywhere before the state file is removed (needs helper_list from pathprofile.sh). It walks
#   files_installed and acts only on entries ending in /agents/fal-runner.md or /c101/fal-mcp.json: removes the agent
#   if it is a symlink to <root>/agents/fal-runner.md (root = this overlay or state overlay_root) or a file carrying the
#   marker; removes the config if it is a regular file identical to the shipped config or matching
#   fal_mcp_config_sha256, then its directory if empty. Anything else is left with a warning. Without a state file it
#   checks the two default paths with the same tests (the uninstaller's no-state fallback). Returns 0; dry-run aware.
#   The agent carries the marker, so the generic files_installed loop (it removes marked files, following links) may
#   already have removed the link; fal_mcp_uninstall then skips it. The config (JSON, no marker) is only removed here.
#
# For the doctor (DR1): source this file and use fal_agent_dest, fal_mcp_config_path, fal_agent_is_ours PATH and
#   fal_config_is_ours PATH (exit 0 = ours). state fal_mcp=true -> the agent link must be ours and resolve (FAIL
#   otherwise); the config is the optional whole-session route, so a missing or edited config is a WARN, not a FAIL.

# The marker substring the uninstaller's files_installed loop greps for; the agent carries it as an HTML comment, so
# BIN_MARKER ("# c101-linux managed file") would not match a copy of it.
FAL_FILE_MARKER="c101-linux managed file"
FAL_MCP_URL=https://mcp.fal.ai/mcp

fal_agent_src() { printf '%s\n' "$C101L_ROOT/agents/fal-runner.md"; }
fal_config_src() { printf '%s\n' "$C101L_ROOT/config/fal-mcp.json"; }
fal_agent_dest() { printf '%s\n' "$HOME/.claude/agents/fal-runner.md"; }
fal_mcp_config_path() { printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/c101/fal-mcp.json"; }

# _fal_state KEY: a state value, or nothing (no state file, key absent, or a dry run on a host without Python yet).
_fal_state() {
  if dry_plan_only; then return 0; fi
  state_get "$1" 2>/dev/null || true
}

# _fal_sha256 FILE: hex sha256 of FILE (coreutils/busybox, then perl's shasum, then the helper Python).
_fal_sha256() {
  local out
  if have sha256sum; then
    out=$(sha256sum <"$1")
  elif have shasum; then
    out=$(shasum -a 256 <"$1")
  else
    find_helper_python
    out=$("${C101L_PY_CMD[@]}" -I -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1")
  fi
  printf '%s\n' "${out%% *}"
}

# fal_agent_is_ours PATH: a symlink to <root>/agents/fal-runner.md (this overlay, or the overlay_root state recorded
# when the checkout has moved since), or a regular file carrying the marker.
fal_agent_is_ours() {
  local p=$1 t root
  if [ -L "$p" ]; then
    t=$(readlink "$p")
    for root in "$C101L_ROOT" "$(_fal_state overlay_root)"; do
      if [ -n "$root" ] && [ "$t" = "$root/agents/fal-runner.md" ]; then return 0; fi
    done
    return 1
  fi
  [ -f "$p" ] && grep -qF -- "$FAL_FILE_MARKER" "$p" 2>/dev/null
}

# fal_config_is_ours PATH: a regular file (not a link) identical to the shipped config, or to what was last installed.
fal_config_is_ours() {
  local p=$1 src rec
  src=$(fal_config_src)
  if [ ! -f "$p" ] || [ -L "$p" ]; then return 1; fi
  if [ -f "$src" ] && cmp -s "$src" "$p"; then return 0; fi
  rec=$(_fal_state fal_mcp_config_sha256)
  [ -n "$rec" ] && [ "$(_fal_sha256 "$p")" = "$rec" ]
}

# _fal_agent_install: link the agent. Returns 1, after a warning, when the destination is not ours or linking fails.
_fal_agent_install() {
  local src dest
  src=$(fal_agent_src)
  dest=$(fal_agent_dest)
  if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$src" ]; then
    ok "$(tilde "$dest") already linked"
  elif { [ -e "$dest" ] || [ -L "$dest" ]; } && ! fal_agent_is_ours "$dest"; then
    warn "$(tilde "$dest") exists and is not ours; not replacing it, so the fal-runner agent is not installed"
    return 1
  else
    if ! run mkdir -p -- "$(dirname -- "$dest")" || ! run ln -sfn -- "$src" "$dest"; then
      warn "could not link $(tilde "$dest"); the fal-runner agent is not installed"
      return 1
    fi
    info "linked $(tilde "$dest") -> $src"
  fi
  state_append files_installed "$(json_str "$dest")"
}

# _fal_config_install: copy the whole-session config. Returns 1, after a warning, when the destination is not ours.
_fal_config_install() {
  local src dest sha
  src=$(fal_config_src)
  dest=$(fal_mcp_config_path)
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$src" "$dest"; then
    ok "$(tilde "$dest") already current"
  elif { [ -e "$dest" ] || [ -L "$dest" ]; } && ! fal_config_is_ours "$dest"; then
    warn "$(tilde "$dest") exists and is not the config this overlay installed (edited, or not ours); left in place. Shipped version: $src"
    return 1
  else
    if ! run mkdir -p -- "$(dirname -- "$dest")" || ! run install -m 0644 -- "$src" "$dest"; then
      warn "could not install $(tilde "$dest"); the whole-session fal MCP config is not installed"
      return 1
    fi
    info "installed $(tilde "$dest") (whole-session fal MCP config)"
  fi
  state_append files_installed "$(json_str "$dest")"
  if [ "$DRY_RUN" != 1 ]; then
    sha=$(_fal_sha256 "$src")
    state_set fal_mcp_config_sha256 "$(json_str "$sha")"
  fi
}

# fal_mcp_install [ENABLE]: see the contract above.
fal_mcp_install() {
  local want=${1:-${FAL_MCP:-${P_FAL_MCP:-0}}} agent cfg agent_ok=0 cfg_ok=0 hint
  agent=$(fal_agent_dest)
  cfg=$(fal_mcp_config_path)
  if [ "$want" != 1 ]; then
    if [ "$(_fal_state fal_mcp)" = true ] && fal_agent_is_ours "$agent"; then
      info "fal MCP agent: kept as installed earlier (remove: rm $(tilde "$agent") $(tilde "$cfg"), or the uninstaller)"
    else
      state_set fal_mcp false
    fi
    return 0
  fi
  step "fal MCP: fal-runner agent and whole-session config"
  [ -r "$(fal_agent_src)" ] || die "missing $(fal_agent_src)"
  [ -r "$(fal_config_src)" ] || die "missing $(fal_config_src)"
  if _fal_agent_install; then agent_ok=1; fi
  if _fal_config_install; then cfg_ok=1; fi
  if [ "$agent_ok" = 1 ]; then state_set fal_mcp true; else state_set fal_mcp false; fi
  hint="fal MCP ($FAL_MCP_URL, key FAL_KEY from ~/.claude/stack.env):"
  if [ "$agent_ok" = 1 ] && [ "$cfg_ok" = 1 ]; then
    info "$hint agent fal-runner loads it only while it runs; whole session: claude --mcp-config $(tilde "$cfg")"
  elif [ "$agent_ok" = 1 ]; then
    info "$hint agent fal-runner loads it only while it runs"
  elif [ "$cfg_ok" = 1 ]; then
    info "$hint whole session: claude --mcp-config $(tilde "$cfg") (agent fal-runner not installed)"
  fi
  return 0
}

# _fal_remove_agent PATH / _fal_remove_config PATH: remove one recorded path if it is still ours.
_fal_remove_agent() {
  local p=$1
  if fal_agent_is_ours "$p"; then
    run rm -f -- "$p"
    info "removed $(tilde "$p") (fal-runner agent)"
  else
    warn "$(tilde "$p") is no longer the fal-runner link this overlay made; left in place"
  fi
}

_fal_remove_config() {
  local p=$1
  if fal_config_is_ours "$p"; then
    run rm -f -- "$p"
    info "removed $(tilde "$p") (whole-session fal MCP config)"
    if [ "$DRY_RUN" != 1 ]; then rmdir -- "$(dirname -- "$p")" 2>/dev/null || true; fi
  else
    warn "$(tilde "$p") differs from the config this overlay installed (edited?); left in place"
  fi
}

# fal_mcp_uninstall: see the contract above.
fal_mcp_uninstall() {
  local installed f
  local -a recorded=() present=()
  if [ -f "$(state_file)" ]; then
    installed=$(_fal_state files_installed)
    if [ -n "$installed" ]; then
      while IFS= read -r f; do
        case $f in */agents/fal-runner.md | */c101/fal-mcp.json) recorded+=("$f") ;; esac
      done < <(helper_list "$installed")
    fi
  else
    recorded=("$(fal_agent_dest)" "$(fal_mcp_config_path)")
  fi
  for f in ${recorded[@]+"${recorded[@]}"}; do
    if [ -e "$f" ] || [ -L "$f" ]; then present+=("$f"); fi
  done
  [ "${#present[@]}" -gt 0 ] || return 0
  step "fal MCP agent and whole-session config"
  for f in "${present[@]}"; do
    case $f in
      */agents/fal-runner.md) _fal_remove_agent "$f" ;;
      */c101/fal-mcp.json) _fal_remove_config "$f" ;;
    esac
  done
  return 0
}
