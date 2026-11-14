# Pitfalls

Each entry: symptom, cause, fix. Entries marked *unverified* are reasoned from documentation or source code, not
observed on a machine. File references into c101 are listed in [UPSTREAM.md](UPSTREAM.md).

## PATH and environment

### 1. Hooks and MCP servers cannot find `uv`, `node`, `npx` or `podman` when started from Claude Desktop
* Symptom: the Code tab reports hook errors (`uv: command not found`), `npx` MCP servers fail to start, the sandbox
  says no container runtime exists. The same stack works from a terminal.
* Cause: a GUI-launched app does not source `~/.bashrc`. Desktop imports the PATH its *login shell* exports, once, at
  start. c101's launchers add only `~/.local/bin`, Homebrew, nix and `/usr/local/bin` (`install.sh:14`, `bin/_pyenv.sh:15`,
  `bin/mcp-run:18`, `bin/neural-memory-mcp:18`, `stack_mcp/desktop_sandbox/config.py:27`): nvm, fnm, volta, asdf, mise,
  linuxbrew, `/snap/bin`, `~/.npm-global/bin`, `~/.bun/bin` are missing.
* Fix: the overlay writes one managed block (`# >>> c101-linux >>>`) into the file the user's login shell reads
  (bash: `~/.bash_profile` if it exists, else `~/.bash_login`, else `~/.profile`; zsh: `${ZDOTDIR:-~}/.zprofile`; fish:
  `~/.config/fish/conf.d/c101-linux.fish`) and, on systemd sessions, `~/.config/environment.d/60-c101-linux.conf`. Then quit
  Desktop completely and start it again. Doctor starts a clean login shell (`env -i ... -lc`) to prove `uv` resolves.

### 2. The PATH block is there but the app still does not see it
* Cause: the app was started before the edit (PATH is imported once); or the session was started by a display manager
  that does not run a login shell for your user (some Wayland sessions), where only `environment.d` applies; or your
  `~/.bash_profile` exists and `~/.profile` was edited instead (bash stops at the first of `.bash_profile`, `.bash_login`,
  `.profile`).
* Fix: log out and in (or restart the app after `systemctl --user import-environment`); the overlay already picks the
  right bash file and writes `environment.d` when a systemd user session exists. `bin/c101-linux-doctor` shows both.

### 3. nvm's node is not found, or is a stale version
* Cause: nvm initialises inside interactive shells (`nvm.sh`), so a non-interactive login shell has no node on PATH. The
  overlay resolves nvm's *default alias* to an absolute directory at install time and writes that directory; if you later
  change the default, the block is stale.
* Fix: re-run `install-linux.sh` (idempotent) to refresh it. Doctor warns when nvm's default has moved. Version managers
  with shims (asdf, mise, volta, fnm's default alias) need no refresh.

### 4. `PATH` contains the same entry many times
* Cause: appending in a profile that is sourced by nested shells. The block skips every directory already on PATH and
  runs `export PATH` once, so sourcing it twice changes nothing (covered by `tests/test_pathblock.py`; not yet run).

### 5. `~` or `$HOME` in an MCP `command` is not expanded
* Cause: Desktop spawns commands without a shell; `~`, relative paths, aliases and functions do not work in
  `claude_desktop_config.json`.
* Fix: `--desktop-chat-mcp` expands a leading `~`/`${HOME}` to the real home at install time. Never hand-write `~`.

## Claude Desktop

### 6. Desktop deletes my edits to `claude_desktop_config.json`
* Cause: the app rewrites the file while it runs and on exit.
* Fix: quit it first. The overlay's merge refuses while a `claude-desktop` process exists (`pgrep`) unless `--force`, backs the file up as
  `claude_desktop_config.json.bak-<UTC stamp>`, refuses to overwrite invalid JSON, and leaves hand-written entries alone (a name collision
  is reported, never overwritten).

### 7. Adding the stack's MCP servers to Desktop makes the Code tab slow or conflicts with neural-memory
* Cause: the Code tab also loads `claude_desktop_config.json` servers, eagerly, in every session. c101's design is lazy
  per-agent loading, and `neural-memory` is owned by one agent (one SQLite writer).
* Fix: `--desktop-chat-mcp` is opt-in, adds only `c101-fetch`, `c101-playwright`, `c101-image`, `c101-desktop-sandbox`, never neural-memory,
  and `uninstall-linux.sh` removes exactly those entries (only when they still equal what was added).
  The HTTP servers (exa, jina, spider, context7) use `headersHelper`, which that file has no equivalent for; add them as connectors in the app.

### 8. Sign-in does not stick / "keyring" errors
* Cause: Desktop stores credentials in the desktop keyring (GNOME Keyring or KWallet) and it must be running and unlocked.
* Fix: `sudo apt install gnome-keyring` (not both gnome-keyring and KWallet on KDE), log in again, unlock. Doctor reports
  the daemon; the installer warns but does not install a keyring itself.

### 9. "Refusing to run as root"
* Cause: Desktop refuses root without `--no-sandbox`; c101 installs into the user's home. The installer refuses too (exit 3)
  unless `--allow-root`, which exists for containers and CI only.

### 10. Desktop or its sandboxed tools fail to start on Ubuntu 24.04 (AppArmor)
* Cause (*unverified for the official .deb*): Ubuntu 23.10+/24.04 set `kernel.apparmor_restrict_unprivileged_userns=1`, which gates
  Chromium's renderer sandbox and bubblewrap. Community packages ship an AppArmor profile; Anthropic's page does not say whether the official
  package does. The checks print a note when the sysctl is 1.
* Fix: test on a clean Ubuntu 24.04 VM; if it fails, the options are the distribution's AppArmor profile for the app or an
  administrator-approved sysctl change. The overlay changes neither.

### 11. Quick Entry hotkey does nothing on Wayland
* Cause: on native Wayland it needs the desktop's GlobalShortcuts portal; on X11 it works directly. Notes only.

### 12. Cowork does not start
* Cause: it runs a QEMU/KVM VM: needs `/dev/kvm` with your user in the `kvm` group, `/dev/vhost-vsock`, qemu, ovmf, virtiofsd.
  Chat and the Code tab do not need it. Doctor reports `/dev/kvm` as ok / no access / absent.

### 13. Computer Use and Dictation are missing
* Cause: Anthropic's Linux build does not ship them. c101's built-in `computer-use` server is macOS-only as well.
  Use c101's `desktop-sandbox` server (container desktop) for GUI tasks.

### 14. Desktop never updates itself
* Cause: no self-update on Linux. `sudo apt upgrade` updates `claude-desktop` and `claude-code` (apt installs of the CLI do not auto-update either).
  The `.deb` may also register its own repository entry (`CLAUDE_DESKTOP_ADD_REPO` in `/etc/default/claude-desktop`): the installer
  skips adding its own source when the repository is already registered in another file, so apt does not warn about duplicates.
  If the package's own entry uses a different `signed-by` path, apt can refuse with "Conflicting values set for option Signed-By" (*unverified*: needs the .deb's postinst); remove one of the two entries.

### 15. The Code tab does not show my c101 agents
* Cause: Anthropic's docs confirm skills from `~/.claude/skills` and shared hooks/settings/CLAUDE.md/MCP; loading of `~/.claude/agents` in the
  Desktop Code tab is not stated (*unverified*). The "Customize" sidebar reads your claude.ai account, not `~/.claude`.
* Fix: try `/agents` in a Code session; the CLI (`--install-cli`, `bin/claude-stack`) is the reference path for agents.

### 16. A second Desktop build is installed and they fight
* Cause: community builds share `~/.config/Claude` with the official package; only one can run. The overlay never installs
  a community build.

## Installation

### 17. Ubuntu 22.04 has Node 12, so the Playwright MCP server fails
* Cause: the distro `nodejs` package is too old (needs >= 18 with npx; Debian 12 and Ubuntu 24.04 ship 18).
* Fix: the installer prints the fix and does not pipe any installer: use mise, fnm, nvm or volta as your user and re-run (the PATH block
  picks them up), or enable your vendor's Node repository yourself. `--node skip` silences it.

### 18. c101's installer fails on a read-only or root-owned checkout
* Cause: `install.sh` creates `.venv`, restores executable bits and writes `__pycache__` in the checkout.
* Fix: use a writable copy (`--source`). The installer checks writability and says so; the test matrix copies the read-only mount first.
  Never run `sudo ./install.sh`: it would leave root-owned files in your checkout. For managed settings the overlay runs only that one
  command as root, with `PYTHONDONTWRITEBYTECODE=1`.

### 19. `install.sh --dry-run` still changed something
* Cause: c101's `install.sh` skips `uv python install` in a dry run but then `exec`s `uv run --project`, which creates/syncs `.venv` and
  populates the uv cache. c101's docs mention it.
* Fix: the overlay's `--dry-run` runs c101's real dry-run entry point (`python -m stack.install --dry-run`) from an existing `.venv`, or only prints the
  command when there is none.

### 20. uv's installer edited my shell profile
* Cause: the upstream installer modifies profiles by default. The overlay sets `UV_NO_MODIFY_PATH=1` and writes its own single block instead.

### 21. Removing the `~/.claude/stack` link broke every session
* Cause: c101 merges hook commands into `~/.claude/settings.json` that call `$HOME/.claude/stack/bin/stack hook ...`; no c101 flag removes them.
* Fix: the uninstaller strips exactly those hook commands (backup first) before it removes the links.

### 22. `claude-stack` through a symlink says `stack: not found`
* Cause: `bin/claude-stack` locates `bin/stack` from `dirname "${BASH_SOURCE[0]}"` without resolving symlinks, so a symlink in
  `~/.local/bin` points it at the wrong directory (c101's `_pyenv.sh` does resolve them).
* Fix: `~/.local/bin/claude-stack`, `stack-sdk` and `c101-stack` are tiny `exec` wrappers, not symlinks.

### 23. Alpine/musl: the Python environment does not sync (*unverified*)
* Cause: some of c101's dependencies (pikepdf, vtracer, resvg-py, pillow) need musllinux wheels or a compiler. The Desktop does not exist for musl anyway.
* Fix: run c101 on a glibc distribution; the matrix reports the Alpine result in OUTCOME.

### 24. Home on NFS
* Cause: Claude Code's installer docs warn about Bus errors when the CLI lives on an NFS home shared across machines; rootless podman's
  overlay storage does not work on NFS and falls back to `vfs` (slow, large) or fails.
* Fix: keep `~/.local/share/claude` and `~/.local/share/containers` on local disk (set `graphroot` in `~/.config/containers/storage.conf`).
  Doctor warns when the storage driver is `vfs`.

## Rootless Podman (the default container engine)

### 25. `podman run` fails with "cannot find newuidmap", "no subuid ranges" or "potentially insufficient UIDs or GIDs available"
* Cause: rootless podman maps container uids onto a range in `/etc/subuid` and `/etc/subgid` and needs the setuid helpers
  `newuidmap`/`newgidmap` (package `uidmap` on Debian/Ubuntu, `shadow-utils` on Fedora, `shadow-uidmap` on Alpine). Users created before the feature, or by
  some provisioning tools, have no range. Ranges must not overlap between users.
* Fix: the overlay installs the packages, checks both files by name and numeric uid, and adds a range after the highest existing one
  (`sudo usermod --add-subuids S-E --add-subgids S-E user`, then `podman system migrate`). Under `--no-sudo` the exact command is printed.

### 26. Rootless podman works in a terminal but not over `ssh`, `su` or `sudo -u`
* Cause: no systemd user session, so `XDG_RUNTIME_DIR` is unset or points to a directory that does not exist; cgroup v2 delegation needs the user manager.
* Fix: log in properly, or `export XDG_RUNTIME_DIR=/run/user/$(id -u)`; for boxes you only reach by SSH, `loginctl enable-linger $USER`
  (`--enable-linger`) keeps `user@UID.service` and your units alive without a session. Doctor shows `XDG_RUNTIME_DIR` and the linger state.

### 27. Containers die when I log out
* Cause: the user manager stops with the last session unless linger is on. Short sandbox runs are unaffected; the Podman API socket and timers are.
* Fix: `--enable-linger`.

### 28. The sandbox starts but every file operation is "permission denied" (rootless; *unverified at runtime*)
* Cause: c101 starts the sandbox with `--user <host uid>:<host gid>` and a 0700 exchange directory owned by that uid
  (`runtime.py:89`, `:242`). In rootless podman the host uid is container *root*; container uid 1000 maps into your subuid range and
  owns nothing.
* Fix: the `c101-podman` wrapper adds `--userns=keep-id` to `podman run`, and `DESKTOP_SANDBOX_RUNTIME` points c101 at it (c101 accepts an
  absolute path). The proper fix is upstream: see UPSTREAM.md item 4. `--no-podman-wrapper` uses plain podman if you prefer your own.

### 29. Fedora/RHEL: bind mounts are "permission denied" with SELinux enforcing (*unverified at runtime*)
* Cause: bind-mounted host directories need a label the container may access (`:Z` / `:z`, or `relabel=` in `--mount`). c101 uses `--mount type=bind`
  without a relabel option.
* Fix: the wrapper adds `--security-opt label=disable` for that one container when `getenforce` says Enforcing (the container is still namespaced, caps are dropped and the network is off).
  To refuse that and relabel yourself: `echo keep > ~/.config/c101-linux/podman-selinux` (c101 starts the wrapper with a minimal environment, so an
  environment variable would never reach it; `C101_PODMAN_SELINUX=keep` only works for manual runs). Relabeling `.stack/sandbox` with `chcon -t container_file_t` is the narrow alternative.

### 30. `podman build` fails with "short-name resolution enforced but cannot prompt"
* Cause: c101's Dockerfile says `FROM debian:bookworm-slim` (`image/Dockerfile:12-13`). Podman needs a registry for short names and cannot ask without a
  TTY (services, timers, IDE terminals).
* Fix: the overlay builds with `--base docker.io/library/debian:bookworm-slim` (a `desktop-sandbox` option) in the installer, the unit and the doctor hint.

### 31. The "docker" socket does not exist: compose, testcontainers or an IDE says `Cannot connect to the Docker daemon`
* Cause: Podman has no daemon; the API socket is a systemd user socket created on demand.
* Fix: `--podman-services` enables the distro's `podman.socket` (or the fallback `c101-podman-api.socket`), and the login-profile block and
  environment.d export `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock` and `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`. Log in again for the
  variables, but only when no real Docker is in use (see 37). Without systemd run `podman system service --time=0 "unix://$XDG_RUNTIME_DIR/podman/podman.sock" &`. The socket path is
  `/run/user/<uid>/podman/podman.sock`; `systemctl --user status podman.socket` shows it.
* Testcontainers: its Ryuk reaper needs a privileged container, which rootless podman refuses; set `TESTCONTAINERS_RYUK_DISABLED=true`
  (left as a commented line in the block).

### 32. Units are installed but "do nothing" / `systemctl --user` says "Failed to connect to bus"
* Cause: no user manager in this context (ssh without a session, su, a container, WSL without `systemd=true`).
* Fix: log in through a real session, or `--enable-linger`, then re-run `--podman-services`; on non-systemd systems the installer skips units and prints the manual command.

### 33. Every `sandbox_start` fails on Ubuntu 22.04: "the requested cgroup controller `cpu` is not available"
* Cause: rootless containers can only use cgroup v2 controllers that systemd delegated to the user's manager. systemd 249 (Ubuntu 22.04) delegates
  `memory pids` only; `cpu` arrived with systemd 252. c101 always passes `--memory`, `--memory-swap`, `--cpus` and `--pids-limit` (`runtime.py:86-88`), so crun refuses the
  first `podman run --cpus` (Debian 12 = 252, Ubuntu 24.04 = 255 and Fedora 41 = 256 are fine). The image build succeeds, which makes this look like a working install.
* Fix: the installer reads `/sys/fs/cgroup/user.slice/user-UID.slice/user@UID.service/cgroup.controllers` and, when `cpu` (or `memory`/`pids`) is missing, writes
  `/etc/systemd/system/user@.service.d/delegate.conf` with `[Service]` / `Delegate=cpu cpuset io memory pids` and runs `systemctl daemon-reload` (through `sudo`;
  under `--no-sudo` the commands and the file are left in `~/.local/state/c101-linux/pending/`). Then log out and in (the user manager must restart). The doctor
  FAILs (`cgroup-delegation`) while it is missing and the sandbox is part of the profile. Without cgroup v2 the check says nothing; boot with
  `systemd.unified_cgroup_hierarchy=1` on a v1 host. Upstream could skip `--cpus` when the controller is absent (UPSTREAM.md 10).

### 34. Podman inside WSL
* Cause: WSL2 without systemd has no user manager (no `podman.socket`, no `XDG_RUNTIME_DIR` unless set), and WSL1 cannot run containers.
* Fix: enable systemd in `/etc/wsl.conf` (`[boot] systemd=true`, then `wsl --shutdown`), or use Docker Desktop's WSL integration on the Windows side and the
  `--container-runtime docker` fallback. The Windows Desktop app is usually the right Desktop.

### 35. Both docker and podman are installed and c101 picks docker
* Cause: c101 searches `docker` before `podman` (`config.py:26`) unless `DESKTOP_SANDBOX_RUNTIME` is set.
* Fix: the profile block exports `DESKTOP_SANDBOX_RUNTIME` (profile `full` or `--sandbox` with PATH integration); your own value always wins. `--docker-shim`
  refuses to install `podman-docker` next to a real Docker (they conflict).

### 36. `docker` group membership as the "fix"
* Cause: docker-group members are root-equivalent. The overlay never adds you to it; with `--container-runtime docker` it only explains how.

### 37. DOCKER_HOST points at Podman and my Docker containers "vanished"
* Cause: exporting `DOCKER_HOST` overrides the active `docker context`. With docker-ce in daily use, `docker ps` would talk to Podman: your dockerd containers,
  images and volumes seem gone, compose stacks are recreated under Podman, ports clash. Nothing is deleted; unsetting the variable undoes it.
* Fix: the installer exports it only when the Podman API unit is enabled and no real Docker is in use (a `/var/run/docker.sock`, a running `docker.service`, a
  non-default `docker context`, or your own `DOCKER_HOST`; a `docker` that is podman's shim does not count). `--docker-host-podman` forces it, `--no-docker-host-podman`
  forbids it. In `environment.d` the value is `${DOCKER_HOST:-unix:///run/user/UID/podman/podman.sock}`, so a value in the manager's environment is never overridden.

### 38. `--no-sudo` printed commands that no longer work after the installer exited
* Cause: the printed `install ... /tmp/c101-linux.XXXX/key ...` referred to the run's temp directory, which is deleted on exit.
* Fix: payloads for root commands are copied to `~/.local/state/c101-linux/pending/` first, and every deferred command is also written to
  `~/.local/state/c101-linux/pending/pending-root-commands.sh` (mode 0600, review it, then `sudo sh` it).

### 39. My `~/.zprofile` / `~/.profile` is a symlink (stow, chezmoi, home-manager) and the link vanished
* Cause: editing by "write a temp file, rename it over the path" replaces the link itself.
* Fix: the helpers resolve the link and edit the real file (the link survives; the backup sits next to the real file). A target in `/nix/store` or a read-only file is refused (exit 4):
  the installer prints the block so you can add it to the configuration that generates the file (a home-manager `programs.zsh.profileExtra`, for example). The same applies to
  `~/.claude/settings.json` and `claude_desktop_config.json` edits.

### 40. The weekly prune deleted my stopped dev container
* Cause: a bare `podman system prune` removes every stopped container, dangling image and unused network of the user.
* Fix: the unit only prunes `dev.c101.desktop-sandbox`-labelled stopped containers older than a week and dangling images carrying c101's sandbox label; volumes and everything else are untouched.

### 41. NixOS: "NixOS cannot run dynamically linked executables" while installing
* Cause: stock NixOS has no `/lib64/ld-linux` for foreign ELF binaries. astral's uv build and the CPython builds `uv python install` downloads (c101's `install.sh` does that)
  are glibc-dynamic. The nixpkgs uv manual warns about the same thing for Python.
* Fix: the installer checks on NixOS, does not fetch astral's binary, and stops with this module snippet unless `nix-ld` is enabled and `uv` comes from nixpkgs:
  ```nix
  { pkgs, ... }: {
    programs.nix-ld.enable = true;
    environment.systemPackages = with pkgs; [ uv git curl nodejs ];
    virtualisation.podman = { enable = true; dockerCompat = true; defaultNetwork.settings.dns_enabled = true; };  # dockerCompat conflicts with virtualisation.docker
  }
  ```
  Rebuild, log in again, re-run. `C101L_SKIP_NIXOS_CHECK=1` continues anyway for people with their own solution (steam-run, a patched Python). Note that c101 pins
  `UV_PYTHON_PREFERENCE=only-managed` in `~/.claude/settings.json`, so Claude Code sessions also use uv's downloaded Python (UPSTREAM.md 12).

### 42. `--dry-run` or the doctor created `~/.local/share/containers`
* Cause: `podman info` initialises rootless storage on first use.
* Fix: neither ever calls it unless the storage directory already exists (the installer's real run probes after installing podman). A test with a fake `podman` guards this.

## Keys, fal image generation, MCP start-up and learning

Added with the parity change of 2026-11-14 (keys and `stack.env`, fal, presets, MCP pre-warm, Playwright's Chromium, the
learning backstop). Every entry is reasoned from the code of both repositories and from primary documentation; none of it
ran on Linux. Entries marked **verify on Linux** name the check that settles them; the full list is in
[VERIFICATION.md](VERIFICATION.md) section 5.

### 43. Playwright MCP: "Chromium distribution 'chrome' is not found" (also arm64 and Alpine)
* Symptom: `browser-operator`, or any agent with the Playwright server, fails at its first browser call with
  `Chromium distribution 'chrome' is not found` and a hint to install Chrome. On arm64 Linux Google Chrome does not exist at all.
* Cause: @playwright/mcp uses the Chrome channel unless told otherwise, and c101 starts it without `--browser`
  (`config/mcp.servers.json:4-15`, UPSTREAM.md 13).
* Fix: `--playwright-browser` (on in standard, full and headless) installs Playwright's own Chromium as your user, no root:
  `npx -y playwright-core@<the version @playwright/mcp@latest pins> install --no-shell chromium`, into `~/.cache/ms-playwright`
  (or `$PLAYWRIGHT_BROWSERS_PATH`, which the server must then see too). It writes `PLAYWRIGHT_MCP_BROWSER=chromium` into the
  login-profile block and `environment.d` (a value you set yourself wins); `--desktop-chat-mcp` entries pass `--browser chromium`.
  Quit Desktop completely and log in again. `error while loading shared libraries` is the separate root step `--playwright-deps`
  (Debian/Ubuntu).
* Profiles that write no profile files (minimal, ci, `--no-path-integration`) cannot carry the variable; the installer says so.
  Add `--path-integration`, or export it where Claude starts.
* `Executable doesn't exist at .../chromium-<revision>/...`: `@playwright/mcp@latest` moved to a newer playwright-core after the
  install, which wants a newer revision. Re-run `install-linux.sh --playwright-browser`.
* arm64: Playwright builds Chromium for Ubuntu 22.04, 24.04 and 26.04 and Debian 12 and 13 (x86-64 and arm64); Ubuntu 20.04 and
  Debian 11 on arm64 have none. Fedora, Arch and other glibc distributions get a generic build with a "not officially supported"
  warning, which works when the libraries are present (*unverified*).
* Alpine/musl, the distro-browser fallback: Playwright's browsers are glibc binaries. Install the distribution's browser
  (`apk add chromium`), skip the download, and export `PLAYWRIGHT_MCP_EXECUTABLE_PATH=/usr/bin/chromium` next to
  `PLAYWRIGHT_MCP_BROWSER=chromium` (check the binary name on your release).
* **Verify on Linux**: whether `environment.d` reaches the stdio MCP servers Desktop starts (`UserManagerEnvironmentTests`), and
  whether the install command leaves the revision the MCP launches and that it starts (`PlaywrightChromiumTests`), both in
  `tests/test_linux_runtime.py`. Doctor: `playwright-browser`.

### 44. The first MCP start times out while `uvx` or `npx` downloads
* Symptom: the first agent that uses `fetch`, `neural-memory` or Playwright reports that the server failed to connect or timed
  out; a later attempt works.
* Cause: c101 starts them as `uvx --python 3.13 ...` and `npx -y @playwright/mcp@latest`. The first launch downloads Python 3.13
  and the package, and Claude Code's start-up timeout runs out first. With c101's per-agent servers this happens in the middle of
  a task, not at session start.
* Fix: `--prewarm-mcp` (on in every profile except ci) starts each server once with `--help` under `timeout 300`, stdin from
  `/dev/null` and without any key variable: fetch and neural-memory always, `@playwright/mcp` with `--playwright-browser`,
  markitdown when it is in `--mcp`. A failure only warns; without uv the warning names uv. Re-run with `--prewarm-mcp` after
  clearing `~/.cache/uv` or `~/.npm/_npx`, or after a new `@playwright/mcp` release. Doctor: `mcp-fetch-cache` and
  `neural-memory-cache` (cache warm or cold). Claude Code's `MCP_TIMEOUT` (milliseconds) raises the start-up timeout
  (*unverified* for agent-scoped servers). Covered by `tests/test_prewarm.py` (stubs; not yet run).

### 45. `stack.env` is a symlink (dotfiles) or lives on a read-only store
* Behaviour: `lib/stackenv.py` writes through a link to its real path (temp file in that directory, mode 600, `os.replace`), so a
  stow or hand-made link survives; the backup lands next to the real file (54). A target in `/nix/store`, on a read-only mount or
  not writable is refused before any prompt (stackenv exit 4, reason `nix-store`, `read-only-mount`, `not-writable` or
  `dir-not-writable`), and the installer reports the names it could not set.
* Fix: make `~/.claude/stack.env` a regular file, or a link to a writable file outside the Nix store, or set `CLAUDE_STACK_ENV`
  to a writable path. That variable must be in the environment Claude starts with (login profile and `environment.d`), or c101's
  hooks and launchers read `~/.claude/stack.env` instead; c101's own installer also creates and tightens only that default path
  (UPSTREAM.md 24).
* The key step tightens a group- or world-readable file to 600 even when nothing else changes, leaves a file owned by another
  account alone (warning), and warns by path when the file's directory (or the link's) belongs to another account or is group or
  world writable: another account could replace the file, and c101's launchers export every name in it (UPSTREAM.md 23).

### 46. Never pass keys on the command line
* Symptom: a key turns up in `~/.bash_history`, `~/.zsh_history`, a CI log or a screen recording.
* Cause: `FAL_KEY=... ./install-linux.sh` is saved by the shell's history, and the value sits in the installer's
  `/proc/<pid>/environ` while it runs (readable by your own user only). No installer option takes a value; values reach
  `lib/stackenv.py` on stdin only.
* Fix: the hidden prompt, `./install-linux.sh --keys` on a first install, `./install-linux.sh --keys --keys-only` later (keys
  and the image backend only; nothing else is re-applied). It asks
  `FAL_KEY` first, then `EXA_API_KEY`, `JINA_API_KEY`, `SPIDER_API_KEY`, `CONTEXT7_API_KEY` (optional) and the extras, with echo
  off. For the environment form without a history entry, start the line with a space (bash: `HISTCONTROL=ignorespace` or
  `ignoreboth`; zsh: `setopt HIST_IGNORE_SPACE`), type the key at the silent `read`, and unset it afterwards. The line below
  is for a first install (a full install); later runs use `./install-linux.sh --keys-only --keys-from-env` instead, which
  touches the keys and the image backend only and never re-applies the profile's defaults over your earlier choices:
  ```sh
   read -rs FAL_KEY && export FAL_KEY && ./install-linux.sh --keys-from-env; unset FAL_KEY
  ```
  In CI, map the secret store's variables to the environment and use `--keys-from-env` (the ci profile never prompts).
* The installer un-exports every key variable on its first line and unsets them after the key step, so c101's installer, the
  pre-warm and the Chromium download never inherit them. Do not put keys into `environment.d`, the login profile, systemd units,
  `claude_desktop_config.json` or `settings.json` yourself: at run time the servers read keys from `stack.env` only (56).

### 47. Image tools are missing until URL, KEY and MODEL are set for an operation
* Symptom: the `image` server is connected but `image-generator` has no `image_generate` (or another operation's tool);
  `image_ops_status` says `NOT CONFIGURED (missing IMAGE_GEN_KEY)`.
* Cause: c101's image router lists an operation's tool only while its `IMAGE_<OP>_URL`, `_MODEL` and `_KEY` are all non-empty
  (`stack_mcp/image_router/config.py:373-384`; the local TRACE and UPSCALE modes and the agent-edited VECTOR_MANIPULATION are the
  exceptions). Without a fal key the installer still writes the fal preset's URL and MODEL lines with empty KEY values, on
  purpose, warns once by name (`FAL_KEY`) and prints `install-linux.sh --keys --keys-only`; on ci and `--yes` runs the same warning also
  gives the unattended form (export `FAL_KEY`, then `install-linux.sh --keys-only --keys-from-env`).
* Fix: add the key (`./install-linux.sh --keys --keys-only`). Check per operation, names and hosts only:
  `~/.claude/stack/bin/image-router --check` (to stderr) or `bin/c101-linux-doctor` (`keys-image`, `image-router`, `image-deps`).
  The router re-reads `stack.env` on every call and announces tool-list changes, so a restart should not be needed (*unverified*).
* An operation you set up with another provider is kept whole and reported by name (`kept_ops`); `--keys-overwrite` replaces
  it with fal, after a backup.

### 48. `FAL_KEY` is not `IMAGE_<OP>_KEY`
* Two readers: `IMAGE_<OP>_KEY` (nine operations: GEN, REF, EDIT, TRANSPARENT, SEGMENT, BG_REMOVAL, UPSCALE, VECTOR, TRACE) is
  what c101's image router sends to `queue.fal.run`; `FAL_KEY` is read only by `bin/mcp-headers fal`, for the hosted fal MCP
  (52). The env file has no variable expansion: `IMAGE_GEN_KEY=${FAL_KEY}` is taken literally (UPSTREAM.md 19).
* The installer asks for one fal key once and writes it to `FAL_KEY` and to every `IMAGE_<OP>_KEY` whose URL is
  `https://queue.fal.run` (exact host). After that they are separate lines: changing `FAL_KEY` alone leaves the image router on the
  old key, and the other way round.
* Fix: rotate with `./install-linux.sh --keys --keys-only --keys-overwrite` (it prompts for every key, set ones included, and
  rewrites all fal lines after a mode-600 backup), or edit all ten lines. Without `--keys-overwrite` a different key is reported
  as kept and only empty fal keys are filled.

### 49. A read-only or root-owned c101 checkout: learned `maxTurns` and `effort` do not stick
* Symptom: `bin/stack learn report` keeps recommending the same change and the agent files never change;
  `~/.claude/stack-data/hook-errors.log` or `learn.log` shows permission errors.
* Cause: c101's learner writes `maxTurns`, `effort` and `autoCompactWindow` into the agent files of the checkout (it follows the
  `~/.claude/agents` links and edits their target). A checkout in `/nix/store`, a root-owned clone or a read-only mount makes every
  write fail; the hooks only log it and sessions go on with the old values.
* Fix: a checkout you own and can write (`--source`, see 18). To stop the attempts, set `auto_apply` to false in c101's
  `config/learning.json`. Doctor: `learning-writable`. The opposite case, a writable git checkout, is 59.

### 50. Quitting Desktop or logging out skips SessionEnd, which loses learning statistics
* Symptom: sessions that ended by quitting Claude Desktop, by logging out or by a crash have no line in
  `~/.claude/stack-data/history.jsonl`; `learn report` counts fewer runs and learned values lag.
* Cause (*unverified*): c101 writes a session's history row, and with `apply_on: session_end` the learned values, only in
  `stack learn run`, which only its SessionEnd hook starts (`stack/hooks.py:496-503`). An app quit, or a logout that stops the
  session scope, may end Claude Code before that hook runs.
* Fix: `--learn-timer` (on in standard, full and headless). A systemd user timer (every 30 minutes, `Persistent=true`) runs
  `~/.local/bin/c101-learn-backstop`, which calls `stack learn run --transcript` for transcripts last written between 30 minutes
  and 3 days ago that no learn run has ingested, at most 20 per run. `learn run` takes c101's `learn.lock` and replaces the
  session's history line, so racing a real SessionEnd is safe. Log: `journalctl --user -u c101-learn-backstop`; preview:
  `c101-learn-backstop --dry-run`. Without a systemd user manager the installer prints a cron line instead. Doctor:
  `learning-timer`.
* Limits of the design: a session idle for more than 30 minutes but still open is learned early and again later; a machine that
  stays off for more than 3 days loses those sessions (knobs `C101_LEARN_BACKSTOP_*`, listed in the script's header); a session
  with no earlier observation file is attributed to the main agent `main` (or the timer's `CLAUDE_STACK_MAIN_AGENT`), so a
  `bin/claude-stack` (delegator) main thread can be mis-attributed.
* **Verify on Linux**: `LearningBackstopRuntimeTests` (timer enabled, active and persistent; a session without SessionEnd gets
  exactly one history row) and the manual `DesktopQuitManualCheck` (steps in the docstring of `tests/test_linux_runtime.py`),
  which also shows whether SessionEnd does run on a Desktop quit; if it does, the backstop simply finds nothing to do.

### 51. "Missing keys" at the start of every session
* Symptom: each session starts with `Missing keys in ~/.claude/stack.env: EXA_API_KEY, JINA_API_KEY, SPIDER_API_KEY (those MCP
  servers run anonymous or fail).`
* Cause: c101's SessionStart hook checks `expected_keys` in its `config/policy.json` (exa, jina, spider), and an empty value counts
  as missing. The installer leaves them empty when no value came from the environment or the prompt (`--yes`, no terminal, the ci
  profile). `FAL_KEY` and the image keys are not in that list, so an empty fal key produces no such line (47).
* Fix: `./install-linux.sh --keys --keys-only`. Without keys exa and jina still work anonymously with rate limits, spider does not
  (doctor `keys-research`: WARN for exa and jina, FAIL for spider outside ci, INFO for context7).
* The line always says `~/.claude/stack.env`, but the hook reads `$CLAUDE_STACK_ENV` when the Claude process has it; a value
  exported only in an interactive shell does not reach Desktop sessions (45).

### 52. The hosted fal MCP: the `fal-runner` agent or `--mcp-config`, not `bin/stack mcp enable fal`
* Symptom: c101's hint (`bin/stack mcp enable fal`, `config/stack.env.example:93`) edits `.mcp.json` and `.claude/settings.json`
  inside the c101 checkout: the server appears only in sessions whose project is that checkout, and `git status` there shows the
  change (UPSTREAM.md 20). Registering it at user scope instead adds about 60 fal tools to every session.
* What the overlay installs by default (loaded only on demand; `--no-fal-mcp` opts out):
  * the agent `fal-runner` (`~/.claude/agents/fal-runner.md`, a link into this overlay). It carries the server inline, so the
    server connects only while the agent runs. Ask for it by name, for example "use fal-runner to turn this image into a
    5-second video": fal's model catalog, video, audio, 3D and the image endpoints c101's `image` server does not wrap, and job
    management. Plain image work goes back to `image-generator`, `image-editor` or `vector-artist`. It starts a paid run without
    asking only up to USD 0.50 per run, or within the ceiling your request names.
  * for a whole session, CLI only: `claude --mcp-config ~/.config/c101/fal-mcp.json` (under `$XDG_CONFIG_HOME` when set). Claude
    Desktop has no such option: use the agent there (whether the Code tab loads `~/.claude/agents` is itself unverified, 15).
* Key: `FAL_KEY` in `stack.env` (48), sent by c101's `bin/mcp-headers fal`. Both the whole-session file and the
  agent run that helper from a fixed path (`$HOME/.claude/stack/bin/mcp-headers`), never from the working directory or
  `$CLAUDE_PROJECT_DIR`: the agent never runs a script from a project directory (the install is always user level; UPSTREAM.md 22).
* Uninstall removes the agent link and the config only while they are still the installed ones (state file); an edited config is
  kept with a warning. Doctor: `fal-runner`, `fal-mcp-config`, `fal-mcp-reach` (an HTTPS probe that sends no credentials).
* Whether fal accepts the key header for tool calls is open: 53.

### 53. fal MCP: key header or OAuth (verify on Linux)
* Symptom: `fal-runner` lists fal's tools, but a call fails with 401 or 403, or the agent reports that the fal MCP refused auth.
* Cause (*unverified*): `bin/mcp-headers fal` sends `Authorization: Key <FAL_KEY>`. c101's note on its `fal` entry
  (`config/mcp.optional.json:8`) says a probe accepted that header for `initialize` and `tools/list`; fal documents OAuth through
  `https://mcp.fal.ai/mcp-relay` as the official route. Tool calls (`run_model`, job submission) with the key header have not
  been tried.
* **Verify on Linux**: no automated test exists (the doctor's probe sends no key on purpose); the manual check is in
  VERIFICATION.md section 5.
* Fallback if the header is refused: the OAuth route, which c101 lists as `fal-oauth` (`config/mcp.optional.json:10-13`): an HTTP
  server with the URL `https://mcp.fal.ai/mcp-relay` and no `headersHelper`. Put that entry into a session config of your own,
  start `claude --mcp-config <that file>` interactively and sign in through `/mcp`. It needs a browser, so not on headless or ci.

### 54. `stack.env.bak-*` files in a dotfiles git repository
* Symptom: `git status` in your dotfiles repository lists `stack.env.bak-<UTC stamp>` files, and `git add -A` would commit
  plain-text keys.
* Cause: before every merge that changes `stack.env` the installer saves the old bytes as `<real path>.bak-<UTC stamp>` (mode 600)
  next to the real file. When `~/.claude/stack.env` is a stow or hand-made link into a repository, the backups land in it. Common
  ignore patterns (`stack.env`, `*.env`, `.env*`, `*.bak`) do not match these names (checked with `git check-ignore` in the
  security review). Nothing deletes them: the installer prints the new backup's path, how many exist and a hint; the uninstaller
  keeps and lists them.
* Fix: add `stack.env.bak-*` (and `stack.env`) to that repository's `.gitignore`, and delete old backups by hand once you no
  longer need them. If one was pushed, rotate the keys in it; rewriting history afterwards is not enough.
* chezmoi copies files by default and is not affected; a home-manager link into `/nix/store` is refused (45).

### 55. `--image-backend none` still installs the image packs on minimal and ci
* Cause: `--image-backend` decides only what goes into `stack.env`. The minimal and ci profiles choose their packs with
  `PRESET=image` (core, image, graphic-design); `none` only stops fal from adding the image preset on the other profiles.
* Fix: choose packs on the command line, which replaces the profile's preset, for example
  `--profile minimal --image-backend none --preset research`, or `--packs core`. `--print-config` shows `PRESET` and `PACKS` before
  anything changes. `keep` acts as the recorded backend, so after an earlier fal install it still adds the image packs.
* `none` is sticky: the state records `image_backend=none`, and a later run without `--image-backend` keeps `none` (the
  profile's fal does not come back, and every printed re-run hint carries `--image-backend none`). Only an explicit
  `--image-backend fal` re-enables fal. `FAL_KEY` is still asked for while the `fal-runner` agent is installed (`--fal-mcp`,
  the default), but a key never turns the image backend back on.

### 56. After editing `stack.env`: restart or reconnect, never reinstall
* At run time the servers take keys only from `~/.claude/stack.env` (or `$CLAUDE_STACK_ENV`); the installer only seeds it. Edit
  the file directly (the mode stays 600: `stat -c %a ~/.claude/stack.env`) or run `./install-linux.sh --keys --keys-only`.
  When an edit takes effect, from c101's code (*unverified on Linux*):
  * the `image` server re-reads the file on every call;
  * the HTTP servers (exa, jina, spider, context7, fal) run `bin/mcp-headers` on each connection: at the next start of an agent
    that carries them, or after `/mcp` reconnect for a whole-session server (`--mcp-config`);
  * stdio servers started through c101's `bin/mcp-run` (fetch, the sandbox) read it at launch: at their next start;
  * servers in `claude_desktop_config.json` (`--desktop-chat-mcp`): after quitting Desktop completely and starting it again;
  * the "missing keys" line (51): in the next session.
* A variable of the same name already in Claude's environment beats the file for servers started through `bin/mcp-run` (c101's
  rule, unless `STACK_ENV_OVERRIDE=1`): one more reason never to export keys from a profile.

### 57. The key step refuses `stack.env` (`line-breaks`), rewrites its line endings or changes its mode
* Symptom: the key step warns and sets nothing, naming `line-breaks`; or a CRLF file comes back with LF; or the mode becomes 600.
* Cause: c101 reads the file with bash (`bin/mcp-run`, `bin/mcp-headers`: lines end at LF only) and with Python (every
  `str.splitlines()` break). A bare CR, NUL, VT, FF, `\x1c`-`\x1e`, U+0085, U+2028 or U+2029 anywhere in the file, even in a
  comment, makes them read different values, so `lib/stackenv.py` refuses the file (exit 3, `line-breaks`) without naming a value.
  A CRLF file (edited on Windows) is rewritten with LF at the next merge, and the backup keeps the original. A file that group or
  others can read is tightened to 600. A key value containing a control character is dropped by name; the other keys still merge.
* Fix: find the lines (this prints line numbers and the text before `=`, never a value), remove the character, re-run:
  ```sh
  python3 -I -c 'import re,sys
  d=open(sys.argv[1],"rb").read().replace(b"\r\n",b"\n").decode("utf-8","replace")
  for n,l in enumerate(d.split("\n"),1):
      if re.search("[\r\0\v\f\x1c-\x1e\x85\u2028\u2029]",l): print(n,l.split("=",1)[0][:40].strip())' ~/.claude/stack.env
  ```

### 58. The uninstaller skipped the "c101 reversal"
* Symptom: `uninstall-linux.sh` warns `c101 reversal (...) skipped: c101's installer would create ~/.claude/stack.env from its
  template` and prints what to undo by hand.
* Cause: c101's installer runs its base step on every invocation, undo flags (`--no-main-agent`, `--no-enforce-models`,
  `--no-python-default`) included, and that step creates `~/.claude/stack.env` from the template when it is missing
  (UPSTREAM.md 24). The uninstaller never creates or changes the keys file, so with the file gone it does not call c101.
* Fix: remove the settings it names from `~/.claude/settings.json` (`agent`, `availableModels`, `enforceAvailableModels`,
  `UV_PYTHON`, `UV_PYTHON_PREFERENCE`), or run c101's installer with the printed flags yourself and delete the recreated template.
* Also by design: the uninstaller never touches `stack.env` or its backups; it lists them, together with Playwright's browsers
  (`~/.cache/ms-playwright` or `$PLAYWRIGHT_BROWSERS_PATH`), the uv cache (`uv cache clean ...`) and `~/.npm/_npx`. A state file
  without a recorded c101 source (only `--keys-only` ran) leaves c101's links and hooks alone.

### 59. `git status` shows modified agent files in c101 (and `agents/fal-runner.md` here)
* Symptom: `git -C ~/.claude/stack status` lists files under `.claude/agents/` as modified, and `git pull` refuses to overwrite
  local changes. The same can happen to `agents/fal-runner.md` in this overlay.
* Cause: c101's learner is on by default (`auto_apply` true, `apply_on` session_end) and rewrites the frontmatter lines
  `maxTurns:`, `effort:` and `autoCompactWindow:` of agents it has evidence for, following the `~/.claude/agents` links into the
  checkouts. It also finds `fal-runner` through `~/.claude/agents` and, with no bounds entry for it, tunes it within its default
  bounds (UPSTREAM.md 21; reasoned from c101's code, not observed).
* Fix: expected, not damage; `git diff` should show only those three keys. Keep them across an update with `git stash`,
  `git pull`, `git stash pop`, or freeze an agent with `"learn": false` in its entry of c101's `config/learning.json` (or turn
  `auto_apply` off, 49).
