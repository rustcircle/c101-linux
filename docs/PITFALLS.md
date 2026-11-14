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
