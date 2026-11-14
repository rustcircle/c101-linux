# shellcheck shell=bash
# shellcheck disable=SC2034  # globals are consumed by the entry points
# shellcheck disable=SC2016  # single quotes keep $HOME literal on purpose: it expands when the profile runs
# pathprofile.sh: make uv, node, npx, docker and friends visible to apps that do not read ~/.bashrc.
#
# Claude Desktop (and its Code tab, hooks and MCP servers) imports the PATH its login shell exports once, at
# start. So the fix is one managed block in the file the user's login shell reads, plus, when a systemd user
# session exists, ~/.config/environment.d/60-c101-linux.conf for sessions that never run a login shell.
# Needs: common.sh, detect.sh (LOGIN_SHELL, HAS_SYSTEMD_USER).

PATH_BEGIN="# >>> c101-linux >>>"
PATH_END="# <<< c101-linux <<<"
ENVD_MARKER="# c101-linux managed file: remove with uninstall-linux.sh"

# _nvm_versions NVMDIR: installed node versions (no leading v), one per line, oldest first.
_nvm_versions() {
  local d
  for d in "$1"/versions/node/v*; do
    [ -d "$d" ] || continue
    printf '%s\n' "${d##*/v}"
  done | sort -V
}

# nvm_default_bin: absolute bin dir of the node that nvm's default alias points to; empty when nvm is absent.
nvm_default_bin() {
  local nvm=${NVM_DIR:-}
  if [ -z "$nvm" ] && [ -d "$HOME/.nvm" ]; then nvm=$HOME/.nvm; fi
  if [ -z "$nvm" ] && [ -d "${XDG_CONFIG_HOME:-$HOME/.config}/nvm" ]; then nvm=${XDG_CONFIG_HOME:-$HOME/.config}/nvm; fi
  if [ -z "$nvm" ] || [ ! -d "$nvm/versions/node" ]; then return 0; fi
  local v="" hop
  [ -r "$nvm/alias/default" ] && v=$(head -n 1 "$nvm/alias/default" | tr -d '[:space:]')
  for hop in 1 2 3; do # lts/* and named aliases are files that point one level further
    if [ -n "$v" ] && [ -r "$nvm/alias/$v" ]; then v=$(head -n 1 "$nvm/alias/$v" | tr -d '[:space:]'); else break; fi
  done
  local pick="" pat
  case $v in
    '' | node | stable | system | lts/* | iojs*) ;; # no usable pin: take the newest installed
    *)
      pat=${v#v}
      pick=$(_nvm_versions "$nvm" | grep -E "^${pat//./\\.}(\\.|\$)" | tail -n 1 || true)
      ;;
  esac
  [ -n "$pick" ] || pick=$(_nvm_versions "$nvm" | tail -n 1 || true)
  if [ -n "$pick" ] && [ -d "$nvm/versions/node/v$pick/bin" ]; then printf '%s\n' "$nvm/versions/node/v$pick/bin"; fi
  return 0
}

# path_candidates: one directory per line, in final precedence order. $HOME stays literal for portability.
path_candidates() {
  local nvm
  printf '%s\n' '$HOME/.local/bin'
  nvm=$(nvm_default_bin)
  if [ -n "$nvm" ]; then
    case $nvm in
      "$HOME"/*) printf '$HOME/%s\n' "${nvm#"$HOME"/}" ;;
      *) printf '%s\n' "$nvm" ;;
    esac
  fi
  printf '%s\n' \
    '$HOME/.volta/bin' \
    '$HOME/.local/share/fnm/aliases/default/bin' \
    '$HOME/.local/share/fnm' \
    '${ASDF_DATA_DIR:-$HOME/.asdf}/shims' \
    '$HOME/.local/share/mise/shims' \
    '$HOME/.bun/bin' \
    '$HOME/.npm-global/bin' \
    '$HOME/.cargo/bin' \
    '/home/linuxbrew/.linuxbrew/bin' \
    '$HOME/.linuxbrew/bin' \
    '/snap/bin'
}

# Entries that could break out of the double quotes the block puts them in are dropped.
_path_safe() { case $1 in *'"'* | *'`'* | *$'\n'*) return 1 ;; esac; return 0; }

# --- extra exports: container runtime for c101's sandbox and the Docker-compatible Podman API ------------------
# ENV_SANDBOX_RUNTIME ("" = none): value for DESKTOP_SANDBOX_RUNTIME. A user's own value always wins.
# ENV_DOCKER_API (0|1): point docker-API clients (docker compose v2, testcontainers, IDEs) at the Podman socket.
# ENV_PLAYWRIGHT_BROWSER ("" = none): value for PLAYWRIGHT_MCP_BROWSER. Playwright MCP defaults to the Chrome
#   channel, which most Linux hosts lack (arm64 never has it); chromium is Playwright's own build, installed at
#   user level by lib/prewarm.sh. A user's own value always wins. Only browser names, never keys, go here (D6).
ENV_SANDBOX_RUNTIME=""
ENV_DOCKER_API=0
ENV_PLAYWRIGHT_BROWSER=chromium

# _pw_browser: ENV_PLAYWRIGHT_BROWSER when it is a plain browser or channel name, else nothing.
_pw_browser() { case $ENV_PLAYWRIGHT_BROWSER in '' | *[!a-z0-9-]*) return 1 ;; esac; printf '%s\n' "$ENV_PLAYWRIGHT_BROWSER"; }

# _home_literal PATH: leading $HOME replaced by the literal text $HOME (expanded when the profile runs).
_home_literal() {
  case $1 in
    "$HOME"/*) printf '$HOME/%s\n' "${1#"$HOME"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

extra_env_sh() {
  local pw
  if pw=$(_pw_browser); then
    printf "# Playwright MCP uses Playwright's Chromium, not the Chrome channel Linux rarely has (set your own to override)\n"
    printf ': "${PLAYWRIGHT_MCP_BROWSER:=%s}"\nexport PLAYWRIGHT_MCP_BROWSER\n' "$pw"
  fi
  if [ -n "$ENV_SANDBOX_RUNTIME" ]; then
    printf '# c101 desktop-sandbox container runtime (c101 reads it before ~/.claude/stack.env; set your own to override)\n'
    printf ': "${DESKTOP_SANDBOX_RUNTIME:=%s}"\nexport DESKTOP_SANDBOX_RUNTIME\n' "$(_home_literal "$ENV_SANDBOX_RUNTIME")"
  fi
  if [ "$ENV_DOCKER_API" = 1 ]; then
    cat <<'EXTRA_SH_EOF'
# Docker API clients talk to Podman's socket (systemd user unit podman.socket or c101-podman-api.socket)
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  : "${DOCKER_HOST:=unix://$XDG_RUNTIME_DIR/podman/podman.sock}"
  : "${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:=$XDG_RUNTIME_DIR/podman/podman.sock}"
  export DOCKER_HOST TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE
  # Testcontainers' Ryuk reaper needs a privileged container, which rootless podman refuses: if it fails, uncomment
  # export TESTCONTAINERS_RYUK_DISABLED=true
fi
EXTRA_SH_EOF
  fi
}

extra_env_fish() {
  local pw
  if pw=$(_pw_browser); then
    printf 'if not set -q PLAYWRIGHT_MCP_BROWSER\n    set -gx PLAYWRIGHT_MCP_BROWSER "%s"\nend\n' "$pw"
  fi
  if [ -n "$ENV_SANDBOX_RUNTIME" ]; then
    printf 'if not set -q DESKTOP_SANDBOX_RUNTIME\n    set -gx DESKTOP_SANDBOX_RUNTIME "%s"\nend\n' "$(_home_literal "$ENV_SANDBOX_RUNTIME")"
  fi
  if [ "$ENV_DOCKER_API" = 1 ]; then
    cat <<'EXTRA_FISH_EOF'
if set -q XDG_RUNTIME_DIR; and not set -q DOCKER_HOST
    set -gx DOCKER_HOST "unix://$XDG_RUNTIME_DIR/podman/podman.sock"
    set -gx TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE "$XDG_RUNTIME_DIR/podman/podman.sock"
    # Testcontainers' Ryuk reaper needs a privileged container, which rootless podman refuses: if it fails, uncomment
    # set -gx TESTCONTAINERS_RYUK_DISABLED true
end
EXTRA_FISH_EOF
  fi
}

extra_env_envd() {
  local pw
  if pw=$(_pw_browser); then
    # environment.d(5) ${VAR:-default}: a value already in the manager's environment wins
    printf 'PLAYWRIGHT_MCP_BROWSER=${PLAYWRIGHT_MCP_BROWSER:-%s}\n' "$pw"
  fi
  if [ -n "$ENV_SANDBOX_RUNTIME" ]; then
    local v=$ENV_SANDBOX_RUNTIME
    case $v in "$HOME"/*) v='${HOME}/'"${v#"$HOME"/}" ;; esac
    # environment.d(5) supports ${VAR:-default}: a value already in the manager's environment is never overridden
    printf 'DESKTOP_SANDBOX_RUNTIME=${DESKTOP_SANDBOX_RUNTIME:-%s}\n' "$v"
  fi
  if [ "$ENV_DOCKER_API" = 1 ]; then
    local uid
    uid=$(id -u)
    printf 'DOCKER_HOST=${DOCKER_HOST:-unix:///run/user/%s/podman/podman.sock}\n' "$uid"
    printf 'TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:-/run/user/%s/podman/podman.sock}\n' "$uid"
    printf '# TESTCONTAINERS_RYUK_DISABLED=true   (uncomment if the Ryuk reaper fails under rootless podman)\n'
  fi
}

# _flat DIR: the variant without ${VAR:-default} (fish does not understand it).
_flat() { printf '%s\n' "${1//'${ASDF_DATA_DIR:-$HOME/.asdf}'/'$HOME/.asdf'}"; }

path_block_sh() {
  local d
  cat <<'EOF_HEAD'
# Managed by c101-linux. PATH for apps started from a login session: Claude Desktop imports it once at start.
# Edit outside this block; `uninstall-linux.sh` removes it.
_c101l_new=""
for _c101l_d in \
EOF_HEAD
  while IFS= read -r d; do
    _path_safe "$d" && printf '  "%s" \\\n' "$d"
  done < <(path_candidates)
  cat <<'EOF_TAIL'
  ; do
  [ -d "$_c101l_d" ] || continue
  case ":$PATH:$_c101l_new:" in *":$_c101l_d:"*) continue ;; esac
  _c101l_new="${_c101l_new:+$_c101l_new:}$_c101l_d"
done
if [ -n "$_c101l_new" ]; then PATH="$_c101l_new:$PATH"; fi
unset _c101l_new _c101l_d
export PATH
EOF_TAIL
  extra_env_sh
}

path_block_fish() {
  local d
  local -a rev=()
  while IFS= read -r d; do
    d=$(_flat "$d")
    _path_safe "$d" && rev=("$d" ${rev[@]+"${rev[@]}"})
  done < <(path_candidates)
  printf '%s\n' '# Managed by c101-linux. PATH for apps started from a login session (listed last-to-first: each is prepended).'
  printf '%s\n' "for d in \\"
  local i last=$((${#rev[@]} - 1))
  for i in "${!rev[@]}"; do
    if [ "$i" -eq "$last" ]; then printf '  "%s"\n' "${rev[i]}"; else printf '  "%s" \\\n' "${rev[i]}"; fi
  done
  printf '%s\n' '  if test -d $d' '    fish_add_path --global --path $d' '  end' 'end'
  extra_env_fish
}

path_envd_content() {
  local d out="" first=1
  while IFS= read -r d; do
    d=$(_flat "$d")
    _path_safe "$d" || continue
    out="${out}${d}:"
    first=0
  done < <(path_candidates)
  printf '%s\n' "$ENVD_MARKER" "# environment.d(5): read by the systemd user manager, so GUI sessions without a login shell get the same PATH."
  if [ "$first" = 0 ]; then printf 'PATH=%s$PATH\n' "$out"; fi
  extra_env_envd
}

login_profile_file() {
  case ${LOGIN_SHELL:-other} in
    bash)
      if [ -e "$HOME/.bash_profile" ]; then
        printf '%s\n' "$HOME/.bash_profile"
      elif [ -e "$HOME/.bash_login" ]; then
        printf '%s\n' "$HOME/.bash_login"
      else
        printf '%s\n' "$HOME/.profile"
      fi
      ;;
    zsh) printf '%s\n' "${ZDOTDIR:-$HOME}/.zprofile" ;;
    fish) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/c101-linux.fish" ;;
    *) printf '%s\n' "$HOME/.profile" ;;
  esac
}

envd_file() { printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/60-c101-linux.conf"; }

envd_wanted() { # envd_wanted MODE(auto|1|0)
  case $1 in
    1) return 0 ;;
    0) return 1 ;;
    *) [ "${HAS_SYSTEMD_USER:-0}" = 1 ] ;;
  esac
}

_helper_dry_flag() { [ "$DRY_RUN" = 1 ] && printf '%s\n' --dry-run; return 0; }

# _refused_block FILE RES: a managed file is a read-only link (home-manager, a /nix/store path): print what to add.
_refused_block() {
  warn "$(tilde "$1") is a link to a file this overlay may not edit ($(json_field "$2" reason)). Add this block to the config that generates it:"
  {
    printf '    %s\n' "$PATH_BEGIN"
    sed 's/^/    /' "$3"
    printf '    %s\n' "$PATH_END"
  } >&2
  next_step "PATH block not written (read-only link): add the printed block to your own dotfiles, then restart Claude Desktop"
}

# path_apply ENVD_MODE: write the login-profile block (and environment.d file when wanted). Idempotent.
path_apply() {
  local envd_mode=${1:-auto} file tmp res changed rc=0
  file=$(login_profile_file)
  if dry_plan_only; then
    info "dry-run (no Python on this host yet, so plan only): would write the PATH block to $(tilde "$file")"
    if envd_wanted "$envd_mode"; then info "dry-run: would write $(tilde "$(envd_file)")"; fi
    return 0
  fi
  tmp=$(tmp_file pathblock)
  if [ "${LOGIN_SHELL:-other}" = fish ]; then path_block_fish >"$tmp"; else path_block_sh >"$tmp"; fi
  # shellcheck disable=SC2046  # the dry-run flag is deliberately absent or a single word
  res=$(helper managed_block.py apply --file "$file" --content-file "$tmp" $(_helper_dry_flag)) || rc=$?
  if [ "$rc" = 4 ]; then
    _refused_block "$file" "$res" "$tmp"
  elif [ "$rc" != 0 ]; then
    die "could not update $file (exit $rc)"
  else
    changed=$(json_field "$res" changed)
    if [ "$changed" = true ]; then
      if [ "$DRY_RUN" = 1 ]; then info "would write PATH block to $(tilde "$file")"; else ok "PATH block written to $(tilde "$file") (backup: $(json_field "$res" backup))"; fi
    else
      ok "PATH block already current in $(tilde "$file")"
    fi
    state_append path_files "$(json_str "$file")"
    if [ "$(json_field "$res" created)" = true ]; then state_append path_files_created "$(json_str "$file")"; fi
    next_step "Quit Claude Desktop completely and start it again (log out/in for GUI sessions): it reads PATH once at launch."
  fi

  if envd_wanted "$envd_mode"; then
    local ef
    ef=$(envd_file)
    path_envd_content >"$tmp"
    rc=0
    # shellcheck disable=SC2046
    res=$(helper managed_block.py write-file --file "$ef" --content-file "$tmp" --marker "$ENVD_MARKER" $(_helper_dry_flag)) || rc=$?
    if [ "$rc" = 4 ]; then
      warn "$(tilde "$ef") is a read-only link; not written ($(json_field "$res" reason))"
    elif [ "$rc" != 0 ]; then
      die "could not write $ef (exit $rc)"
    else
      if [ "$(json_field "$res" changed)" = true ]; then
        if [ "$DRY_RUN" = 1 ]; then info "would write $(tilde "$ef")"; else ok "wrote $(tilde "$ef")"; fi
      else
        ok "$(tilde "$ef") already current"
      fi
      state_set environment_d "$(json_str "$ef")"
    fi
  fi
  rm -f -- "$tmp"
}

# path_remove: undo path_apply using the state file; tolerant of a missing state (falls back to defaults).
path_remove() {
  local files created f ef dry=""
  [ "$DRY_RUN" = 1 ] && dry=--dry-run
  files=$(state_get path_files 2>/dev/null || true)
  created=$(state_get path_files_created 2>/dev/null || true)
  if [ -n "$files" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      local del=""
      case $created in *"\"$f\""*) del=--delete-if-empty ;; esac
      # shellcheck disable=SC2086  # $dry and $del are empty or one word
      helper managed_block.py remove --file "$f" $del $dry >/dev/null && info "PATH block removed from $(tilde "$f")"
    done < <(helper_list "$files")
  fi
  ef=$(state_get environment_d 2>/dev/null || true)
  if [ -n "$ef" ]; then
    # shellcheck disable=SC2086
    helper managed_block.py remove-file --file "$ef" --marker "$ENVD_MARKER" $dry >/dev/null && info "removed $(tilde "$ef")"
  fi
}

# helper_list JSON_LIST: one string per line.
helper_list() {
  find_helper_python
  "${C101L_PY_CMD[@]}" -I -c '
import json, sys
for x in json.loads(sys.argv[1]):
    print(x)
' "$1"
}

# path_check_file FILE: exit 0 when FILE holds exactly one block.
path_check_file() { helper managed_block.py check --file "$1" >/dev/null 2>&1; }
