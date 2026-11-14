# shellcheck shell=bash
# shellcheck disable=SC2034  # PREWARM_FAILED is read by the entry point
# prewarm.sh: make c101's stdio MCP servers start on first use. Their first launch downloads a Python or npm
# package (uvx, npx) and Claude's MCP start-up timeout runs out before it finishes; Playwright MCP also defaults
# to the Chrome channel, which most Linux hosts lack and arm64 never has, so it gets Playwright's own Chromium.
#
# Contract for install-linux.sh (node I1)
#   Source after common.sh, detect.sh and deps.sh. Call both functions at the top level of main(), after
#   ensure_uv/ensure_node, after c101_run (the neural-memory version is read from the c101 checkout) and after
#   the key variables were unset. Each function prints its own step header and is a no-op unless its profile key
#   is 1, so calling it inside `if [ "$P_<KEY>" = 1 ]` is fine but not required.
#   prewarm_step             P_PREWARM_MCP=1: uvx --python 3.13 mcp-server-fetch --help and
#                            uvx --python 3.13 --from neural-memory==<NM_VERSION> nmem-mcp --help, always;
#                            npx -y @playwright/mcp@latest --help only when P_PLAYWRIGHT_BROWSER=1;
#                            uvx --python 3.13 markitdown-mcp --help only when markitdown is in OPT_MCP.
#   playwright_browser_step  P_PLAYWRIGHT_BROWSER=1: user-level Chromium for Playwright MCP (R1 decision, below).
#                            No root, no --with-deps (system libraries stay with --playwright-deps). musl/Alpine
#                            and hosts without a Playwright build are skipped with guidance.
#   Globals read: P_PREWARM_MCP, P_PLAYWRIGHT_BROWSER, P_PLAYWRIGHT_DEPS (hint only), OPT_MCP (comma list),
#                 C101_SRC, DRY_RUN, ARCH, LIBC, OS_ID, OS_VERSION, OS_FAMILY, PLAYWRIGHT_BROWSERS_PATH (if set).
#   Globals set:  PREWARM_FAILED (labels that failed or were skipped for a missing tool).
#   Returns:      0 always. A failure is a WARN plus a next step, never die: the server still works, it just
#                 downloads on its first start instead.
#   Every child runs through run (common.sh: --dry-run prints it and runs nothing), under `timeout 300`, with
#   stdin from /dev/null (a server that does start sees EOF and exits) and without the stack's key variables in
#   its environment (D6: the servers read keys from ~/.claude/stack.env only).
# Needs: common.sh (run, warn, info, ok, step, next_step, have, version_ge, tilde), detect.sh, deps.sh (node_state).

PREWARM_FAILED=()

# --- R1 decision (nodes/R1/decision.md): the ONE place that names the browser and how it is installed -------
# playwright-core is pinned to the exact version @playwright/mcp@latest depends on, so the browser revision
# matches the one the MCP launches. `chromium` is the full build (chrome-for-testing); --no-shell skips the
# headless shell, which the MCP never starts.
PW_MCP_SPEC=@playwright/mcp@latest
pw_r1_commands() { # pw_r1_commands VERSION: sets PW_VERSION_CMD and PW_INSTALL_CMD
  PW_VERSION_CMD=(npm view "$PW_MCP_SPEC" dependencies.playwright-core)
  PW_INSTALL_CMD=(npx -y "playwright-core@${1:-}" install --no-shell chromium)
}

# _prewarm_secret_name NAME: true for the variables a pre-warm child must not inherit (keys belong to stack.env).
_prewarm_secret_name() {
  case $1 in
    IMAGE_* | FAL_* | *_API_KEY | *_API_TOKEN) return 0 ;;
  esac
  return 1
}

# _prewarm_exec CMD...: the single place a pre-warm or browser-install child starts. Prints the child's output
# (stdout and stderr), or the dry-run line; returns the child's status (124 = timed out).
_prewarm_exec() {
  local n
  local -a scrub=()
  while IFS= read -r n; do
    if _prewarm_secret_name "$n"; then scrub+=(-u "$n"); fi
  done < <(compgen -e 2>/dev/null || true)
  run timeout 300 env ${scrub[@]+"${scrub[@]}"} "$@" </dev/null 2>&1
}

# _prewarm LABEL CMD...: one pre-warm; success is quiet, failure only warns.
_prewarm() {
  local label=$1 out rc=0
  shift
  [ "$DRY_RUN" = 1 ] || info "pre-warming $label (first download, up to 300 s)"
  out=$(_prewarm_exec "$@") || rc=$?
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s\n' "$out"
    return 0
  fi
  if [ "$rc" = 0 ]; then
    ok "$label is cached"
    return 0
  fi
  if [ "$rc" = 124 ]; then
    warn "pre-warm of $label timed out after 300 s; it downloads on its first start instead"
  else
    warn "pre-warm of $label failed (exit $rc); it downloads on its first start instead"
  fi
  printf '%s\n' "$out" | tail -n 5 | sed 's/^/    /' >&2
  PREWARM_FAILED+=("$label")
  return 0
}

# prewarm_nm_version: NM_VERSION="x.y.z" from c101's bin/neural-memory-mcp (source checkout, then ~/.claude/stack).
prewarm_nm_version() {
  local f v re='^[0-9][0-9A-Za-z.+!-]*$'
  local -a files=("$HOME/.claude/stack/bin/neural-memory-mcp")
  if [ -n "${C101_SRC:-}" ]; then files=("$C101_SRC/bin/neural-memory-mcp" "${files[@]}"); fi
  for f in "${files[@]}"; do
    [ -r "$f" ] || continue
    v=$(sed -nE "s/^NM_VERSION=[\"']?([^\"' ]+).*/\\1/p" "$f" | head -n 1)
    if [[ $v =~ $re ]]; then
      printf '%s\n' "$v"
      return 0
    fi
  done
  return 1
}

# _prewarm_node_ok: node >= NODE_MIN_MAJOR with npx (deps.sh's node_state), else just npx on PATH.
_prewarm_node_ok() {
  if declare -F node_state >/dev/null; then
    node_state
    if [ "${NODE_OK:-0}" = 1 ]; then return 0; fi
    return 1
  fi
  have npx
}

_prewarm_mcp_listed() { case ",${OPT_MCP:-}," in *",$1,"*) return 0 ;; esac; return 1; }

prewarm_step() {
  [ "${P_PREWARM_MCP:-0}" = 1 ] || return 0
  step "MCP server pre-warm (so the first start does not time out while uvx/npx download)"
  if [ "$DRY_RUN" != 1 ] && ! have timeout; then
    warn "timeout (coreutils) is not installed: MCP pre-warm skipped; the servers download on their first start"
    PREWARM_FAILED+=(all)
    return 0
  fi
  local nmv
  local -a uv_names=(fetch neural-memory)
  if _prewarm_mcp_listed markitdown; then uv_names+=(markitdown); fi
  if have uvx || [ "$DRY_RUN" = 1 ]; then
    have uvx || info "dry-run: uvx is not on PATH yet (a real run installs uv first)"
    _prewarm fetch uvx --python 3.13 mcp-server-fetch --help
    # nmem-mcp parses its arguments with argparse before any first-run setup (neural-memory 4.62.0,
    # mcp/server.py main), so --help never creates a data dir or touches ~/.claude/settings.json.
    if nmv=$(prewarm_nm_version); then
      _prewarm "neural-memory $nmv" uvx --python 3.13 --from "neural-memory==${nmv}" nmem-mcp --help
    elif [ "$DRY_RUN" = 1 ]; then
      info "dry-run: neural-memory's version comes from c101's bin/neural-memory-mcp (NM_VERSION), not on disk yet"
    else
      warn "NM_VERSION not found in c101's bin/neural-memory-mcp: neural-memory not pre-warmed"
      PREWARM_FAILED+=(neural-memory)
    fi
    if _prewarm_mcp_listed markitdown; then _prewarm markitdown uvx --python 3.13 markitdown-mcp --help; fi
  else
    warn "uv is not installed (no uvx on PATH): ${uv_names[*]} not pre-warmed. Install uv (https://docs.astral.sh/uv/), then re-run $(rerun_cmd --prewarm-mcp)"
    PREWARM_FAILED+=("${uv_names[@]}")
  fi
  if [ "${P_PLAYWRIGHT_BROWSER:-0}" = 1 ]; then
    if [ "$DRY_RUN" = 1 ] || _prewarm_node_ok; then
      _prewarm playwright npx -y "$PW_MCP_SPEC" --help
    else
      warn "Node.js >= ${NODE_MIN_MAJOR:-18} with npx is not installed: @playwright/mcp not pre-warmed (see --node)"
      PREWARM_FAILED+=(playwright)
    fi
  fi
  if [ "$DRY_RUN" != 1 ] && [ "${#PREWARM_FAILED[@]}" -gt 0 ]; then
    next_step "MCP pre-warm incomplete (${PREWARM_FAILED[*]}): those servers may time out on their first start; re-run $(rerun_cmd --prewarm-mcp)"
  fi
  return 0
}

# _pw_no_build: prints why Playwright ships no Chromium for this host (amd64 or arm64 on a recent distro only).
_pw_no_build() {
  case ${ARCH:-} in
    amd64) return 1 ;;
    arm64) ;;
    *)
      printf '%s\n' "Playwright ships Chromium for amd64 and arm64 only (this is ${ARCH:-unknown})"
      return 0
      ;;
  esac
  local v=${OS_VERSION:-}
  [ -n "$v" ] || return 1
  if { [ "${OS_ID:-}" = ubuntu ] && ! version_ge "$v" 22.04; } || { [ "${OS_ID:-}" = debian ] && ! version_ge "$v" 12; }; then
    printf '%s\n' "Playwright has no arm64 Chromium for ${OS_ID} ${v} (arm64 needs Ubuntu 22.04+ or Debian 12+)"
    return 0
  fi
  return 1
}

playwright_browser_step() {
  [ "${P_PLAYWRIGHT_BROWSER:-0}" = 1 ] || return 0
  step "Playwright browser (Chromium, user level, for PLAYWRIGHT_MCP_BROWSER=chromium)"
  if [ "${LIBC:-unknown}" = musl ] || [ "${OS_FAMILY:-}" = apk ]; then
    warn "Playwright's Chromium builds need glibc and this is musl (Alpine): no browser downloaded."
    info "Fallback: install the distro's browser as root (apk add chromium) and add"
    info "  export PLAYWRIGHT_MCP_EXECUTABLE_PATH=/usr/bin/chromium   next to PLAYWRIGHT_MCP_BROWSER=chromium"
    info "to your login profile, then restart Claude. Never use a Chromium build from an unofficial source."
    next_step "Alpine/musl: apk add chromium (as root) and export PLAYWRIGHT_MCP_EXECUTABLE_PATH=/usr/bin/chromium for Playwright MCP"
    return 0
  fi
  local why
  if why=$(_pw_no_build); then
    warn "$why: Playwright's Chromium not installed, so Playwright MCP cannot start a browser on this host"
    return 0
  fi
  if [ "$DRY_RUN" != 1 ]; then
    if ! have timeout; then
      warn "timeout (coreutils) is not installed: Playwright's Chromium not installed"
      return 0
    fi
    if ! _prewarm_node_ok || ! have npm; then
      warn "Node.js >= ${NODE_MIN_MAJOR:-18} with npm and npx is not installed: Playwright's Chromium not installed (see --node)"
      next_step "Install Node.js >= ${NODE_MIN_MAJOR:-18}, then re-run $(rerun_cmd --playwright-browser)"
      return 0
    fi
  fi
  [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ] ||
    info "PLAYWRIGHT_BROWSERS_PATH is set: the browser goes there, so Claude must see the same value"

  local ver rc=0 out
  pw_r1_commands ""
  ver=$(_prewarm_exec "${PW_VERSION_CMD[@]}") || rc=$?
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s\n' "$ver"
    ver="<version-from-npm-view>"
  else
    ver=$(printf '%s\n' "$ver" | tail -n 1)
    ver=${ver//[\'\"[:space:]]/}
    if [ "$rc" != 0 ] || ! [[ $ver =~ ^[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*$ ]]; then
      warn "could not read the playwright-core version of $PW_MCP_SPEC (npm view, exit $rc): Chromium not installed"
      next_step "Playwright's Chromium was not installed: re-run $(rerun_cmd --playwright-browser) when npm can reach the registry"
      return 0
    fi
  fi
  pw_r1_commands "$ver"
  [ "$DRY_RUN" = 1 ] || info "installing Chromium for playwright-core $ver into $(tilde "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}") (up to 300 s)"
  rc=0
  out=$(_prewarm_exec "${PW_INSTALL_CMD[@]}") || rc=$?
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s\n' "$out"
    return 0
  fi
  if [ "$rc" != 0 ]; then
    if [ "$rc" = 124 ]; then warn "Chromium download timed out after 300 s"; else warn "Chromium install failed (exit $rc)"; fi
    printf '%s\n' "$out" | tail -n 5 | sed 's/^/    /' >&2
    next_step "Playwright's Chromium is missing: re-run $(rerun_cmd --playwright-browser)"
    return 0
  fi
  local d where=""
  for d in "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"/chromium-*; do
    if [ -d "$d" ]; then where=" in $(tilde "$d")"; fi
  done
  ok "Chromium for playwright-core $ver$where"
  info "if @playwright/mcp@latest later moves to a new Playwright, the MCP reports \"Executable doesn't exist\": re-run $(rerun_cmd --playwright-browser)"
  if [ "${OS_FAMILY:-}" = apt ] && [ "${P_PLAYWRIGHT_DEPS:-0}" != 1 ]; then
    info "Chromium also needs system libraries (root): add --playwright-deps if the browser does not start"
  fi
  return 0
}
