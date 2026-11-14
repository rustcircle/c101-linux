#!/usr/bin/env bash
# install-linux.sh: install the c101 agent stack on Linux for Claude Desktop (Code tab) and the Claude Code CLI.
# c101 itself is consumed unmodified; this overlay adds the Linux and Desktop layer around its installer.
# Usage: install-linux.sh [--profile minimal|standard|full|headless|ci] [options]   (see --help)
if [ -z "${BASH_VERSION:-}" ]; then
  echo "install-linux.sh needs bash (>= 4): run it as ./install-linux.sh or 'bash install-linux.sh'" >&2
  exit 2
fi
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "install-linux.sh needs bash >= 4 (this is ${BASH_VERSION})." >&2
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

usage() {
  cat <<USAGE_EOF
$PROG $C101L_VERSION: c101 agent stack for Claude Desktop and Claude Code on Linux (Linux only)

Usage: $PROG [options]

Profile
  --profile NAME         minimal | standard (default) | full | headless | ci   (config/profiles/*.conf)
  --print-config         print the resolved plan as KEY=VALUE and exit (no changes)

c101 source
  --source PATH          existing c101 checkout (default: \$C101_SOURCE, then ../c101 next to this repo, then a clone)
  --repo URL             clone origin (default $DEFAULT_C101_REPO)
  --ref REF              branch, tag or commit to clone (default $DEFAULT_C101_REF)
  --packs a,b            c101 install packs (passed through; core is always included)
  --mcp a,b              optional servers from c101's config/mcp.optional.json (passed through; Adobe/peekaboo refused)
  --main-agent | --no-main-agent
  --enforce-models | --no-enforce-models
  --managed-settings     also write /etc/claude-code/managed-settings.json (exact model allowlist, needs root)
  --link-bins | --no-link-bins    ~/.local/bin/{claude-stack,stack-sdk,c101-stack} (profile default applies)

Claude clients (signed Anthropic repositories only; nothing community is ever fetched)
  --install-desktop      Claude Desktop from the official APT repo (Debian 12+/Ubuntu 22.04+, amd64/arm64);
                         elsewhere prints guidance
  --install-cli          Claude Code CLI (signed apt repo on Debian/Ubuntu, else Anthropic's installer); skipped if present
  --desktop-chat-mcp     add the curated c101-* MCP servers to claude_desktop_config.json (opt-in, see README)
  --force                allow that merge while Claude Desktop is running (it will overwrite the file on exit)

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
OPT_PROFILE=standard
OPT_SOURCE="" OPT_REPO="" OPT_REF="" OPT_PACKS="" OPT_MCP=""
OPT_MAIN_AGENT="" OPT_ENFORCE_MODELS="" OPT_PATH_INTEGRATION="" OPT_ENVIRONMENT_D=""
OPT_SANDBOX="" OPT_PLAYWRIGHT_DEPS="" OPT_NO_SUDO="" OPT_YES="" OPT_LINK_BINS=""
OPT_CONTAINER_RUNTIME="" OPT_PODMAN_SERVICES="" OPT_PODMAN_WRAPPER=1 OPT_DOCKER_HOST_PODMAN=""
OPT_PODMAN_TIMERS=0 OPT_DOCKER_SHIM=0 OPT_ENABLE_LINGER=0 OPT_MANAGED_SETTINGS=0
OPT_INSTALL_DESKTOP=0 OPT_INSTALL_CLI=0 OPT_DESKTOP_CHAT_MCP=0 OPT_FORCE=0
OPT_NODE=auto OPT_ALLOW_ROOT=0 OPT_PRINT_CONFIG=0 OPT_HELP=0

parse_args() {
  local -a a=()
  local x
  for x in "$@"; do # --opt=value -> --opt value
    case $x in --*=*) a+=("${x%%=*}" "${x#*=}") ;; *) a+=("$x") ;; esac
  done
  set -- ${a[@]+"${a[@]}"}
  while [ $# -gt 0 ]; do
    case $1 in
      --profile | --source | --repo | --ref | --packs | --node | --mcp | --container-runtime)
        [ $# -ge 2 ] || usage_error "$1 needs a value"
        case $1 in
          --profile) OPT_PROFILE=$2 ;;
          --source) OPT_SOURCE=$2 ;;
          --repo) OPT_REPO=$2 ;;
          --ref) OPT_REF=$2 ;;
          --packs) OPT_PACKS=$2 ;;
          --node) OPT_NODE=$2 ;;
          --mcp) OPT_MCP=$2 ;;
          --container-runtime) OPT_CONTAINER_RUNTIME=$2 ;;
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

  PROFILE=$OPT_PROFILE
  profile_load "$PROFILE"
  apply_overrides
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
  if [ "$IMMUTABLE" = nixos ]; then nixos_check; fi
  resolve_source
  info "c101 source: $C101_SRC ($C101_SRC_KIND)"
  info "c101 flags : $(c101_flag_string)"
  info "containers : runtime=$P_CONTAINER_RUNTIME sandbox=$P_SANDBOX services=$P_PODMAN_SERVICES"

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

  step "c101 installer"
  if [ "$DRY_RUN" = 1 ] && [ ! -d "$C101_SRC" ]; then
    info "dry-run: c101 is not on disk yet, so its own plan cannot be shown"
  else
    c101_run
  fi
  if [ -n "$OPT_MCP" ] && ! have claude; then
    warn "--mcp: c101 registers optional servers through the claude CLI, which is not installed: it printed the manual commands (or use --install-cli and re-run)"
  fi

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

  if [ "$OPT_DESKTOP_CHAT_MCP" = 1 ]; then desktop_chat_mcp_apply "$OPT_FORCE"; fi

  if [ "$P_PLAYWRIGHT_DEPS" = 1 ]; then
    step "Playwright system libraries"
    setup_playwright_deps
  fi

  if [ "$DRY_RUN" != 1 ]; then
    state_set version "\"$C101L_VERSION\""
    state_set profile "$(json_str "$PROFILE")"
    state_set updated "$(json_str "$(utc_stamp)")"
    state_set overlay_root "$(json_str "$C101L_ROOT")"
    state_set container_runtime "$(json_str "$P_CONTAINER_RUNTIME")"
    if [ "$C101_SRC_KIND" = clone ]; then state_set c101_clone "$(json_str "$C101_SRC")"; fi
    log ""
    log "state: $(tilde "$(state_file)")"
  fi

  next_step "Fill in API keys in ~/.claude/stack.env (this installer never reads it), then run: $C101L_ROOT/bin/c101-linux-doctor"
  if [ "$P_DESKTOP_CHECKS" = 1 ] && ! desktop_installed; then
    next_step "Claude Desktop is not installed here. Official build: re-run with --install-desktop (Debian/Ubuntu); see docs/SCENARIOS.md"
  fi
  print_manual_and_next
  if [ "$DRY_RUN" = 1 ]; then
    log ""
    log "dry run complete: no files were changed."
  fi
}

main "$@"
