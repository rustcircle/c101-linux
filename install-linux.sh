#!/usr/bin/env bash
# install-linux.sh: install the c101 agent stack on Linux for Claude Desktop (Code tab) and the Claude Code CLI.
# c101 itself is consumed unmodified; this overlay adds the Linux and Desktop layer around its installer.
# Usage: install-linux.sh [--profile minimal|standard|full|headless|ci] [options]   (see --help)
if [ -z "${BASH_VERSION:-}" ]; then
  echo "install-linux.sh needs bash (>= 4): run it as ./install-linux.sh or 'bash install-linux.sh'" >&2
  exit 2
fi
if [ "${BASH_VERSINFO[0]}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 2 ]; }; then
  echo "install-linux.sh needs bash >= 4.2 (this is ${BASH_VERSION})." >&2
  exit 2
fi
set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

C101L_VERSION=0.1.0
C101L_ROOT=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly C101L_ROOT C101L_VERSION
PROG=${0##*/}

# shellcheck source=lib/common.sh
. "$C101L_ROOT/lib/common.sh"
# shellcheck source=lib/detect.sh
. "$C101L_ROOT/lib/detect.sh"
# shellcheck source=lib/profiles.sh
. "$C101L_ROOT/lib/profiles.sh"
# shellcheck source=lib/deps.sh
. "$C101L_ROOT/lib/deps.sh"
# shellcheck source=lib/c101.sh
. "$C101L_ROOT/lib/c101.sh"
# shellcheck source=lib/desktop.sh
. "$C101L_ROOT/lib/desktop.sh"
# shellcheck source=lib/pathprofile.sh
. "$C101L_ROOT/lib/pathprofile.sh"
# shellcheck source=lib/services.sh
. "$C101L_ROOT/lib/services.sh"
# Parity libraries. keys.sh resets its inputs (KEYS, IMAGE_BACKEND, KEYS_OVERWRITE) when sourced, so it must come
# before parse_args: a value inherited from the caller's environment never counts.
# shellcheck source=lib/keys.sh
. "$C101L_ROOT/lib/keys.sh"
# shellcheck source=lib/presets.sh
. "$C101L_ROOT/lib/presets.sh"
# shellcheck source=lib/prewarm.sh
. "$C101L_ROOT/lib/prewarm.sh"
# shellcheck source=lib/learning.sh
. "$C101L_ROOT/lib/learning.sh"
# shellcheck source=lib/falmcp.sh
. "$C101L_ROOT/lib/falmcp.sh"

usage() {
  cat <<USAGE_EOF
$PROG $C101L_VERSION: c101 agent stack for Claude Desktop and Claude Code on Linux (Linux only)

Usage: $PROG [options]

Profile
  --profile NAME         minimal | standard (default) | full | headless | ci   (config/profiles/*.conf)
                         (without --profile, the profile recorded by an earlier install stays; standard on a first run)
  --print-config         print the resolved plan as KEY=VALUE and exit (no changes)

c101 source
  --source PATH          existing c101 checkout (default: \$C101_SOURCE, then ../c101 next to this repo, then a clone)
  --repo URL             clone origin (default $DEFAULT_C101_REPO)
  --ref REF              branch, tag or commit to clone (default $DEFAULT_C101_REF)
  --packs a,b            c101 install packs (core is always included; unioned with --preset)
  --mcp a,b              optional servers from c101's config/mcp.optional.json (passed through; Adobe/peekaboo refused).
                         The research servers exa, jina, spider, fetch and context7 are always registered by c101.
  --main-agent | --no-main-agent
  --enforce-models | --no-enforce-models
  --managed-settings     also write /etc/claude-code/managed-settings.json (exact model allowlist, needs root)
  --link-bins | --no-link-bins    ~/.local/bin/{claude-stack,stack-sdk,c101-stack} (profile default applies)

Presets (config/presets.conf; core is always installed)
  --preset LIST          $(presets_names 2>/dev/null || true)
                         repeatable; unioned with each other and with --packs, and together they replace the
                         profile's preset (minimal, ci: image; standard, full, headless: everything)
  --pick-preset          choose presets from a numbered menu (needs a terminal)

Image generation (fal, c101's main use) and API keys
  Keys live in ~/.claude/stack.env (\$CLAUDE_STACK_ENV; mode 600). This installer only seeds that file through
  lib/stackenv.py; it never prints a value, and no key ever goes to argv, settings, units or the state file.
  --image-backend fal|none|keep   fal (every profile's default): one FAL_KEY configures c101's image server and adds
                         the image + graphic-design packs; none: image settings untouched; keep: as configured
  --keys                 ask (hidden input) for each missing key on a terminal, even with --yes: FAL_KEY first,
                         then EXA_API_KEY, JINA_API_KEY, SPIDER_API_KEY, CONTEXT7_API_KEY (optional), then extras.
                         Default (profile KEYS=auto): environment variables, then the prompt when interactive
  --keys-from-env        keys only from environment variables of those names, never a prompt (headless, ci)
  --keys-only            only seed stack.env (keys and image backend), then exit: no c101 installer, no other step
  --keys-overwrite       replace values already in stack.env (a mode-600 backup first); without it they are kept
  --fal-mcp | --no-fal-mcp   hosted fal MCP (mcp.fal.ai) as the lazy agent fal-runner in ~/.claude/agents plus
                         ~/.config/c101/fal-mcp.json for 'claude --mcp-config'; never registered globally (default on)

MCP start-up and learning
  --prewarm-mcp | --no-prewarm-mcp   download fetch and neural-memory now (and @playwright/mcp with the browser,
                         markitdown when in --mcp), so their first start does not time out (default on, ci off)
  --playwright-browser | --no-playwright-browser   Playwright's own Chromium at user level (no root) and
                         PLAYWRIGHT_MCP_BROWSER=chromium in the login profile (standard, full, headless)
  --learn-timer | --no-learn-timer   systemd user timer that runs c101's learning for sessions that ended without
                         SessionEnd, e.g. a Desktop quit (standard, full, headless)

Claude clients (signed Anthropic repositories only; nothing community is ever fetched)
  --install-desktop      Claude Desktop from the official APT repo (Debian 12+/Ubuntu 22.04+, amd64/arm64);
                         elsewhere prints guidance
  --install-cli          Claude Code CLI (signed apt repo on Debian/Ubuntu, else Anthropic's installer); skipped if present
  --desktop-chat-mcp     add the curated c101-* MCP servers to claude_desktop_config.json (opt-in, see README)
  --force                allow that merge while Claude Desktop is running (it will overwrite the file on exit);
                         it never touches stack.env (that is --keys-overwrite)

Containers (rootless Podman by default; Docker is only a fallback)
  --sandbox              build c101's desktop-sandbox image (full profile does this)
  --container-runtime podman|docker   (default podman)
  --podman-services | --no-podman-services
                         systemd user units: Docker-compatible API socket, sandbox image refresh, prune (full does this)
  --podman-timers        also enable the weekly c101-sandbox-image and c101-podman-prune timers
  --docker-shim          install the distro's podman-docker package ('docker' runs podman); never next to real Docker
  --docker-host-podman | --no-docker-host-podman
                         export DOCKER_HOST (and TESTCONTAINERS_*) pointing at Podman's socket. Default auto: only when
                         the API unit is enabled and no Docker daemon, context or DOCKER_HOST is already in use
  --enable-linger        loginctl enable-linger so user units run without a login session (SSH/headless)
  --no-podman-wrapper    use plain podman as the sandbox runtime instead of ~/.local/bin/c101-podman

Environment
  --node auto|distro|skip   Node >= 18 for the Playwright MCP server (default auto: distro package, else guidance)
  --playwright-deps      install Chromium's system libraries (Debian/Ubuntu, needs root)
  --path-integration | --no-path-integration   login-profile PATH block (profile default applies)
  --environment-d | --no-environment-d         ~/.config/environment.d file (default: auto = systemd user session)

Behaviour
  --no-sudo              never run sudo; print privileged commands instead
  -y, --yes              non-interactive
  -n, --dry-run          change nothing (also passed through to c101's installer)
  --allow-root           run as root anyway (containers/CI); Claude Desktop itself refuses root
  -h, --help

Not available on Linux: --adobe (Adobe apps run on macOS/Windows only) and c101's built-in computer-use server;
the desktop-sandbox server (a container desktop) is the computer-use route here.
Exit codes: 0 ok, 1 failure, 2 usage, 3 refused (not Linux, or root without --allow-root).
USAGE_EOF
}

# --- argument parsing ----------------------------------------------------------------------------------------------
OPT_PROFILE=standard OPT_PROFILE_EXPLICIT=0
OPT_SOURCE="" OPT_REPO="" OPT_REF="" OPT_PACKS="" OPT_MCP=""
OPT_MAIN_AGENT="" OPT_ENFORCE_MODELS="" OPT_PATH_INTEGRATION="" OPT_ENVIRONMENT_D=""
OPT_SANDBOX="" OPT_PLAYWRIGHT_DEPS="" OPT_NO_SUDO="" OPT_YES="" OPT_LINK_BINS=""
OPT_CONTAINER_RUNTIME="" OPT_PODMAN_SERVICES="" OPT_PODMAN_WRAPPER=1 OPT_DOCKER_HOST_PODMAN=""
OPT_PODMAN_TIMERS=0 OPT_DOCKER_SHIM=0 OPT_ENABLE_LINGER=0 OPT_MANAGED_SETTINGS=0
OPT_INSTALL_DESKTOP=0 OPT_INSTALL_CLI=0 OPT_DESKTOP_CHAT_MCP=0 OPT_FORCE=0
OPT_NODE=auto OPT_ALLOW_ROOT=0 OPT_PRINT_CONFIG=0 OPT_HELP=0
# Parity options: empty = the profile decides. KEYS_OVERWRITE (lib/keys.sh) is set by --keys-overwrite and nothing else.
OPT_KEYS="" OPT_IMAGE_BACKEND="" OPT_KEYS_ONLY=0 OPT_PRESET="" OPT_PICK_PRESET=0
OPT_FAL_MCP="" OPT_PREWARM_MCP="" OPT_PLAYWRIGHT_BROWSER="" OPT_LEARN_TIMER=""

parse_args() {
  local -a a=()
  local x
  for x in "$@"; do # --opt=value -> --opt value
    case $x in --*=*) a+=("${x%%=*}" "${x#*=}") ;; *) a+=("$x") ;; esac
  done
  set -- ${a[@]+"${a[@]}"}
  while [ $# -gt 0 ]; do
    case $1 in
      --profile | --source | --repo | --ref | --packs | --node | --mcp | --container-runtime | --image-backend | --preset)
        [ $# -ge 2 ] || usage_error "$1 needs a value"
        case $1 in
          --profile) OPT_PROFILE=$2 OPT_PROFILE_EXPLICIT=1 ;;
          --source) OPT_SOURCE=$2 ;;
          --repo) OPT_REPO=$2 ;;
          --ref) OPT_REF=$2 ;;
          --packs) OPT_PACKS=$2 ;;
          --node) OPT_NODE=$2 ;;
          --mcp) OPT_MCP=$2 ;;
          --container-runtime) OPT_CONTAINER_RUNTIME=$2 ;;
          --image-backend) OPT_IMAGE_BACKEND=$2 ;;
          --preset) OPT_PRESET="${OPT_PRESET:+$OPT_PRESET,}$2" ;; # repeatable: values are unioned
        esac
        shift # the value; the loop's own shift drops the option
        ;;
      --adobe | --adobe-no-setup | --no-adobe)
        usage_error "$1 is not available on Linux: the Adobe apps (InDesign, Illustrator, Photoshop, After Effects, Premiere Pro) exist for macOS and Windows only. For GUI automation on Linux use the desktop-sandbox server (a disposable container desktop with LibreOffice, Inkscape and friends)."
        ;;
      --install-desktop) OPT_INSTALL_DESKTOP=1 ;;
      --install-cli) OPT_INSTALL_CLI=1 ;;
      --desktop-chat-mcp) OPT_DESKTOP_CHAT_MCP=1 ;;
      --force) OPT_FORCE=1 ;;
      --sandbox) OPT_SANDBOX=1 ;;
      --playwright-deps) OPT_PLAYWRIGHT_DEPS=1 ;;
      --podman-services) OPT_PODMAN_SERVICES=1 ;;
      --no-podman-services) OPT_PODMAN_SERVICES=0 ;;
      --podman-timers) OPT_PODMAN_TIMERS=1 ;;
      --docker-shim) OPT_DOCKER_SHIM=1 ;;
      --docker-host-podman) OPT_DOCKER_HOST_PODMAN=1 ;;
      --no-docker-host-podman) OPT_DOCKER_HOST_PODMAN=0 ;;
      --enable-linger) OPT_ENABLE_LINGER=1 ;;
      --no-podman-wrapper) OPT_PODMAN_WRAPPER=0 ;;
      --managed-settings) OPT_MANAGED_SETTINGS=1 ;;
      --link-bins) OPT_LINK_BINS=1 ;;
      --no-link-bins) OPT_LINK_BINS=0 ;;
      --path-integration) OPT_PATH_INTEGRATION=1 ;;
      --no-path-integration) OPT_PATH_INTEGRATION=0 ;;
      --environment-d) OPT_ENVIRONMENT_D=1 ;;
      --no-environment-d) OPT_ENVIRONMENT_D=0 ;;
      --main-agent) OPT_MAIN_AGENT=1 ;;
      --no-main-agent) OPT_MAIN_AGENT=0 ;;
      --enforce-models) OPT_ENFORCE_MODELS=1 ;;
      --no-enforce-models) OPT_ENFORCE_MODELS=0 ;;
      --keys) OPT_KEYS=prompt ;;
      --keys-from-env) OPT_KEYS='env' ;;
      --keys-only) OPT_KEYS_ONLY=1 ;;
      --keys-overwrite) KEYS_OVERWRITE=1 ;; # the only switch to stackenv.py merge --overwrite (D2; --force is unrelated)
      --pick-preset) OPT_PICK_PRESET=1 ;;
      --fal-mcp) OPT_FAL_MCP=1 ;;
      --no-fal-mcp) OPT_FAL_MCP=0 ;;
      --prewarm-mcp) OPT_PREWARM_MCP=1 ;;
      --no-prewarm-mcp) OPT_PREWARM_MCP=0 ;;
      --playwright-browser) OPT_PLAYWRIGHT_BROWSER=1 ;;
      --no-playwright-browser) OPT_PLAYWRIGHT_BROWSER=0 ;;
      --learn-timer) OPT_LEARN_TIMER=1 ;;
      --no-learn-timer) OPT_LEARN_TIMER=0 ;;
      --no-sudo) OPT_NO_SUDO=1 ;;
      -y | --yes) OPT_YES=1 ;;
      -n | --dry-run) DRY_RUN=1 ;;
      --allow-root) OPT_ALLOW_ROOT=1 ;;
      --print-config) OPT_PRINT_CONFIG=1 ;;
      -h | --help) OPT_HELP=1 ;;
      *) usage_error "unknown option: $1" ;;
    esac
    shift
  done
  case $OPT_NODE in auto | distro | skip) ;; *) usage_error "--node must be auto, distro or skip" ;; esac
  case $OPT_CONTAINER_RUNTIME in '' | podman | docker) ;; *) usage_error "--container-runtime must be podman or docker" ;; esac
  [[ -z $OPT_PACKS || $OPT_PACKS =~ ^[a-z0-9,-]+$ ]] || usage_error "--packs takes a comma list of pack names"
  [[ -z $OPT_MCP || $OPT_MCP =~ ^[a-z0-9,-]+$ ]] || usage_error "--mcp takes a comma list of server names"
  if [ -n "$OPT_MCP" ]; then refuse_unsupported_mcp "$OPT_MCP"; fi
  [[ -z $OPT_REF || $OPT_REF =~ ^[A-Za-z0-9._/-]+$ ]] || usage_error "--ref has unexpected characters"
  [[ -z $OPT_REPO || $OPT_REPO =~ ^[^[:space:]]+$ ]] || usage_error "--repo must not contain spaces"
  case $OPT_IMAGE_BACKEND in '' | fal | none | keep) ;; *) usage_error "--image-backend must be fal, none or keep" ;; esac
  if [ "$OPT_KEYS_ONLY" = 1 ] && [ "$OPT_PICK_PRESET" = 1 ]; then
    usage_error "--pick-preset has no effect with --keys-only (presets are applied by a full install)"
  fi
}

# key_env_unexport: the key variables of lib/keys.sh (FAL_KEY ... RUNWARE_API_KEY, IMAGE_<OP>_*) stay readable as shell
# variables for the key step, but are no longer exported, so no child started before that step (package managers,
# git, uv, c101's installer) inherits a key. keys_step then unsets them for good (keys_scrub_env, P7/D6).
key_env_unexport() {
  local n
  for n in "${KEYS_ORDER[@]}"; do export -n "${n?}" 2>/dev/null || true; done
  for n in "${!IMAGE_@}"; do
    if [[ $n =~ $KEYS_OP_RE ]]; then export -n "${n?}" 2>/dev/null || true; fi
  done
  export -n KEYS IMAGE_BACKEND KEYS_OVERWRITE 2>/dev/null || true # reset by keys.sh; never passed on either
  return 0
}

# image_backend_planned: the backend the key step will apply, for the pack implication before anything runs. keep
# means the backend an earlier run recorded (as lib/keys.sh does for state), so a recorded fal keeps its image packs.
# State is read only when the helper Python already exists (no download here).
image_backend_planned() {
  local prev=""
  case $IMAGE_BACKEND in
    fal | none)
      printf '%s\n' "$IMAGE_BACKEND"
      return 0
      ;;
  esac
  if helper_python_available; then prev=$(state_get image_backend 2>/dev/null) || prev=""; fi
  case $prev in fal | none) printf '%s\n' "$prev" ;; *) printf 'keep\n' ;; esac
}

# image_backend_sticky: D12. `--image-backend none` is sticky: with no --image-backend on this command line and state
# image_backend=none, IMAGE_BACKEND stays none, so neither the profile's fal (every profile's default) nor a printed
# re-run hint turns fal image generation back on; only an explicit `--image-backend fal` does. FAL_KEY is still asked
# and reported for the fal-runner agent (FAL_MCP=1). State is read only when the helper Python already exists.
IMAGE_BACKEND_STICKY=0
image_backend_sticky() {
  local prev=""
  IMAGE_BACKEND_STICKY=0
  if [ -n "$OPT_IMAGE_BACKEND" ] || [ "$IMAGE_BACKEND" = none ]; then return 0; fi
  if helper_python_available; then prev=$(state_get image_backend 2>/dev/null) || prev=""; fi
  if [ "$prev" = none ]; then
    IMAGE_BACKEND=none
    IMAGE_BACKEND_STICKY=1
  fi
  return 0
}

# profile_sticky: R1. With no --profile on this command line and a profile recorded in state, that profile stays: a hint
# or a plain re-run never turns a minimal, headless or ci host into a standard one. An explicit --profile wins. State is
# read only when the helper Python already exists and the recorded profile still has a config/profiles/NAME.conf.
profile_sticky() {
  local prev=""
  if [ "$OPT_PROFILE_EXPLICIT" = 1 ]; then return 0; fi
  if helper_python_available; then prev=$(state_get profile 2>/dev/null) || prev=""; fi
  if [ -z "$prev" ] || ! profile_valid_name "$prev" || [ ! -r "$C101L_ROOT/config/profiles/$prev.conf" ]; then return 0; fi
  OPT_PROFILE=$prev
  info "profile: $prev, as the earlier install recorded it (--profile NAME changes it)"
}

# keys_fal_mcp_effective: 1 when the fal-runner agent is (to be) installed, for the FAL_KEY wording of keys_summary. A
# --keys-only run changes no overlay step, so unless --fal-mcp/--no-fal-mcp was given it follows the recorded fal_mcp
# (R6), not the profile default.
keys_fal_mcp_effective() {
  local v=${P_FAL_MCP:-1} prev=""
  if [ "$OPT_KEYS_ONLY" = 1 ] && [ -z "$OPT_FAL_MCP" ] && helper_python_available; then
    prev=$(state_get fal_mcp 2>/dev/null) || prev=""
    case $prev in true) v=1 ;; false) v=0 ;; esac
  fi
  printf '%s\n' "$v"
}

# record_image_backend: state image_backend = the effective backend of the key step (fal|none|keep; keep never
# replaces a recorded fal or none: lib/keys.sh resolves it). Never a key name or value.
record_image_backend() {
  [ "$DRY_RUN" != 1 ] || return 0
  [ -n "${KEYS_BACKEND_EFFECTIVE:-}" ] || return 0
  helper_python_available || return 0
  state_set image_backend "$(json_str "$KEYS_BACKEND_EFFECTIVE")"
}

# keys_summary: closing next steps about stack.env from the key step's results; key NAMES and fixed words only.
# Every re-run command comes from lib/keys.sh's builders (D14): `install-linux.sh --keys --keys-only`, the user's
# literal `install-linux.sh --keys` (hidden prompt, run from a terminal) limited to the keys and image-backend step, so
# a fix never re-applies the profile's defaults (FAL_MCP, PRESET) over an earlier install's choices. Interactive runs
# print keys_rerun_cmd alone; ci and --yes runs print keys_rerun_hint, that command AND the unattended form, the key
# exported first, then `install-linux.sh --keys-only --keys-from-env` (D13). Each carries --profile and a non-fal
# effective backend (D12).
keys_summary() {
  local f n missing="" how eff=${KEYS_BACKEND_EFFECTIVE:-}
  f=${KEYS_STACK_ENV:-$(keys_stack_env_path)}
  how="add them with $(keys_rerun_hint)"
  if [ "${KEYS_FAL_MISSING:-0}" = 1 ]; then
    if keys_unattended; then
      next_step "fal image generation (c101's main use) needs FAL_KEY: run $(keys_rerun_hint "" FAL_KEY)"
    else
      next_step "fal image generation (c101's main use) needs FAL_KEY: $(keys_rerun_cmd) asks for it (hidden input); FAL_KEY=... before the command also works but lands in your shell history"
    fi
  fi
  for n in ${KEYS_MISSING:-}; do
    if [ "$n" = FAL_KEY ]; then
      if [ "${KEYS_FAL_MISSING:-0}" = 1 ]; then continue; fi
      if [ -n "$eff" ] && [ "$eff" != fal ]; then
        # D12: with the image backend off, only the hosted fal MCP (the fal-runner agent) uses FAL_KEY.
        if [ "$(keys_fal_mcp_effective)" != 1 ]; then continue; fi
        n="FAL_KEY (used by the fal-runner agent only; the image backend stays $eff, and a key does not change that: --image-backend fal does)"
      fi
    fi
    missing+="${missing:+, }$n"
  done
  if [ -n "$missing" ]; then
    next_step "Keys still empty in $(tilde "$f"): $missing (exa and jina work without a key at lower rate limits, spider needs one, CONTEXT7_API_KEY is optional); $how or edit the file"
  fi
  next_step "API keys live in $(tilde "$f") (mode 600), the only place c101's servers read them from: this installer only seeds it through lib/stackenv.py and never reads back, prints or logs a value (key names only); the servers re-read it on every start, so after an edit restart Claude or reconnect with /mcp (no re-install)"
}

cleanup() {
  if [ -n "${C101L_TMP:-}" ]; then rm -rf -- "$C101L_TMP"; fi
  return 0
}

on_error() {
  local rc=$?
  err "$PROG failed at line $1 (exit $rc): $2"
  err "the overlay is idempotent: fix the cause and re-run"
  return "$rc"
}

main() {
  key_env_unexport # before the first child process
  parse_args "$@"
  if [ "$OPT_HELP" = 1 ]; then
    usage
    exit 0
  fi

  # Platform and privilege gates come before anything is read or written.
  if [ "$(uname -s)" != Linux ]; then
    err "this installer is Linux only (found $(uname -s)). It configures Claude Desktop's Linux build and a Linux shell environment."
    exit 3
  fi
  if [ "$(effective_uid)" -eq 0 ]; then
    if [ "$OPT_ALLOW_ROOT" != 1 ]; then
      err "refusing to run as root: c101 installs into the user's home, and Claude Desktop refuses to run as root."
      err "run as your normal user (sudo is used only for package installs), or pass --allow-root in a container or CI."
      exit 3
    fi
    warn "running as root (--allow-root): Claude Desktop will not start as root; this is for containers and CI"
  fi

  profile_sticky
  PROFILE=$OPT_PROFILE
  profile_load "$PROFILE"
  apply_overrides
  profile_check_bool FAL_MCP PREWARM_MCP PLAYWRIGHT_BROWSER LEARN_TIMER
  # Key step inputs (lib/keys.sh): the profile values after the flags were folded in. KEYS_OVERWRITE stays as
  # parse_args left it (1 only after --keys-overwrite).
  KEYS=${P_KEYS:-0}
  IMAGE_BACKEND=${P_IMAGE_BACKEND:-keep}
  image_backend_sticky # D12: a recorded none stays none unless --image-backend was given
  keys_validate
  # Presets and packs (lib/presets.sh, D9): a command-line selection replaces the profile's PRESET + C101_PACKS. fal
  # brings the image preset (image + graphic-design packs; a no-op when every pack is selected).
  # shellcheck disable=SC2153  # P_PRESET is set by profile_load (printf -v from PROFILE_KEYS)
  presets_resolve "$P_PRESET" "$P_C101_PACKS" "$OPT_PRESET" "$OPT_PACKS" "$OPT_PICK_PRESET"
  if [ "$(image_backend_planned)" = fal ]; then presets_add_preset image; fi
  P_C101_PACKS=$PRESET_PACKS
  # PLAYWRIGHT_MCP_BROWSER=chromium goes into the login-profile block only when this run installs that Chromium.
  if [ "$P_PLAYWRIGHT_BROWSER" != 1 ]; then ENV_PLAYWRIGHT_BROWSER=""; fi
  if [ "$OPT_PRINT_CONFIG" = 1 ]; then
    print_config
    exit 0
  fi

  C101L_TMP=$(mktemp -d "${TMPDIR:-/tmp}/c101-linux.XXXXXX")
  trap cleanup EXIT
  trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

  step "c101-linux $C101L_VERSION  profile=$PROFILE"
  if [ "$DRY_RUN" = 1 ]; then log "    DRY RUN: nothing will be changed."; fi
  info "$P_DESCRIPTION"
  detect_all
  detect_summary
  if [ "$IMMUTABLE" = nixos ] && [ "$OPT_KEYS_ONLY" != 1 ]; then nixos_check; fi
  resolve_source
  info "c101 source: $C101_SRC ($C101_SRC_KIND)"
  if [ "$IMAGE_BACKEND_STICKY" = 1 ]; then
    info "image backend: none, as an earlier --image-backend none recorded it (the profile's fal does not turn it back on; --image-backend fal does)"
  fi

  # --keys-only: stack.env and the image_backend state entry only; no c101 installer, no other overlay step.
  if [ "$OPT_KEYS_ONLY" = 1 ]; then
    keys_only
    record_image_backend
    if [ "$DRY_RUN" != 1 ] && helper_python_available; then state_set updated "$(json_str "$(utc_stamp)")"; fi
    keys_summary
    print_manual_and_next
    if [ "$DRY_RUN" = 1 ]; then
      log ""
      log "dry run complete: no files were changed."
    fi
    exit 0
  fi

  info "c101 flags : $(c101_flag_string)"
  info "presets    : $(presets_summary)"
  info "containers : runtime=$P_CONTAINER_RUNTIME sandbox=$P_SANDBOX services=$P_PODMAN_SERVICES"
  info "keys       : keys=$KEYS image-backend=$IMAGE_BACKEND fal-mcp=$P_FAL_MCP prewarm=$P_PREWARM_MCP browser=$P_PLAYWRIGHT_BROWSER learn-timer=$P_LEARN_TIMER"

  if [ "$P_DESKTOP_CHECKS" = 1 ]; then desktop_prechecks; fi

  step "Prerequisites"
  local need_git=0
  if [ "$C101_SRC_KIND" = clone ]; then need_git=1; fi
  ensure_prereqs "$need_git"
  ensure_uv
  ensure_node "$OPT_NODE"

  if [ "$OPT_INSTALL_CLI" = 1 ]; then install_cli; fi
  if [ "$OPT_INSTALL_DESKTOP" = 1 ]; then install_desktop; fi

  step "c101 checkout"
  fetch_source
  presets_validate_packs # every pack name (presets, --packs, fal's image preset) against c101's packs.json

  step "c101 installer"
  if [ "$DRY_RUN" = 1 ] && [ ! -d "$C101_SRC" ]; then
    info "dry-run: c101 is not on disk yet, so its own plan cannot be shown"
  else
    c101_run
  fi
  if [ -n "$OPT_MCP" ] && ! have claude; then
    warn "--mcp: c101 registers optional servers through the claude CLI, which is not installed: it printed the manual commands (or use --install-cli and re-run: $(rerun_cmd --install-cli --mcp "$OPT_MCP"))"
  fi

  # API keys and the fal image backend: seeds stack.env (lib/stackenv.py), then unsets every key variable, so no
  # later child (pre-warm, browser download, podman, systemctl) inherits one.
  keys_step
  keys_scrub_env
  record_image_backend # R4: right after the key step, so a later failure still leaves the backend recorded (D12 stickiness)
  # Hosted fal MCP: the lazy fal-runner agent and the --mcp-config file; never a user-scope registration (D8).
  fal_mcp_install "$P_FAL_MCP"

  if [ "$P_LINK_BINS" = 1 ]; then
    step "Command links"
    link_bins
  fi
  if [ "$OPT_MANAGED_SETTINGS" = 1 ]; then
    step "Managed settings"
    managed_settings_apply
  fi

  # Containers: rootless Podman is the default engine; everything below is skipped when nothing asks for it.
  local want_podman=0
  if [ "$P_CONTAINER_RUNTIME" = podman ]; then
    if [ "$P_SANDBOX" = 1 ] || [ "$P_PODMAN_SERVICES" = 1 ] || [ "$OPT_DOCKER_SHIM" = 1 ]; then want_podman=1; fi
  fi
  if [ "$want_podman" = 1 ]; then
    step "Rootless Podman"
    setup_podman
  fi
  if [ "$P_SANDBOX" = 1 ]; then
    step "desktop-sandbox image"
    setup_sandbox
    ENV_SANDBOX_RUNTIME=$(sandbox_runtime_value)
  fi
  if [ "$P_PODMAN_SERVICES" = 1 ]; then
    if [ "$P_CONTAINER_RUNTIME" = podman ]; then
      install_podman_services "$OPT_PODMAN_TIMERS" "$(sandbox_runtime_value)"
      docker_host_decision "$P_DOCKER_HOST_PODMAN"
    else
      warn "--podman-services is for the podman runtime; skipped with --container-runtime docker"
    fi
  fi
  if [ "$OPT_DOCKER_SHIM" = 1 ]; then install_docker_shim; fi
  if [ "$OPT_ENABLE_LINGER" = 1 ]; then enable_linger; fi

  # Learning backstop timer: its own profile key, independent of the Podman services (systemd user units either way).
  learn_timer_install "$P_LEARN_TIMER"

  # Podman is the default sandbox runtime in every profile that writes the login-profile block: put the wrapper in
  # place and export DESKTOP_SANDBOX_RUNTIME even when this profile does not install podman itself (standard, headless).
  if [ "$P_PATH_INTEGRATION" = 1 ] && [ -z "$ENV_SANDBOX_RUNTIME" ]; then
    if [ "$P_CONTAINER_RUNTIME" = podman ] && [ "$OPT_PODMAN_WRAPPER" = 1 ]; then install_podman_wrapper || OPT_PODMAN_WRAPPER=0; fi
    ENV_SANDBOX_RUNTIME=$(sandbox_runtime_value)
  fi
  if [ "$P_PATH_INTEGRATION" = 1 ]; then
    step "PATH and environment for GUI-launched apps"
    path_apply "$P_ENVIRONMENT_D"
  elif [ "$ENV_DOCKER_API" = 1 ] || [ -n "$ENV_SANDBOX_RUNTIME" ]; then
    warn "profile '$PROFILE' writes no profile files, so DESKTOP_SANDBOX_RUNTIME/DOCKER_HOST are not exported; add --path-integration"
  fi
  if [ "$P_PATH_INTEGRATION" != 1 ] && [ "$P_PLAYWRIGHT_BROWSER" = 1 ]; then
    next_step "Playwright MCP needs PLAYWRIGHT_MCP_BROWSER=chromium in the environment Claude starts with (this profile writes no profile files): add --path-integration, or export it yourself"
  fi

  if [ "$OPT_DESKTOP_CHAT_MCP" = 1 ]; then desktop_chat_mcp_apply "$OPT_FORCE"; fi

  if [ "$P_PLAYWRIGHT_DEPS" = 1 ]; then
    step "Playwright system libraries"
    setup_playwright_deps
  fi

  # Playwright's Chromium and the MCP pre-warm: after the PATH block (uv and node are on PATH since ensure_uv and
  # ensure_node) and after the key step, so no child sees a key. Each is gated on its own profile key.
  if [ "$P_PLAYWRIGHT_BROWSER" = 1 ]; then playwright_browser_step; fi
  if [ "$P_PREWARM_MCP" = 1 ]; then prewarm_step; fi

  if [ "$DRY_RUN" != 1 ]; then
    state_set version "\"$C101L_VERSION\""
    state_set profile "$(json_str "$PROFILE")"
    state_set updated "$(json_str "$(utc_stamp)")"
    state_set overlay_root "$(json_str "$C101L_ROOT")"
    state_set container_runtime "$(json_str "$P_CONTAINER_RUNTIME")"
    if [ "$C101_SRC_KIND" = clone ]; then state_set c101_clone "$(json_str "$C101_SRC")"; fi
    state_set preset "$(json_str "$PRESET_RESOLVED")"
    state_set packs "$(json_str "$PRESET_PACKS")"
    record_image_backend
    log ""
    log "state: $(tilde "$(state_file)")"
  fi

  keys_summary
  next_step "Run the checks: $C101L_ROOT/bin/c101-linux-doctor"
  if [ "$P_DESKTOP_CHECKS" = 1 ] && ! desktop_installed; then
    next_step "Claude Desktop is not installed here. Official build: re-run $(rerun_cmd --install-desktop) (Debian/Ubuntu); see docs/SCENARIOS.md"
  fi
  print_manual_and_next
  if [ "$DRY_RUN" = 1 ]; then
    log ""
    log "dry run complete: no files were changed."
  fi
}

main "$@"
