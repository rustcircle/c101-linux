# c101Linux

> **Status: version 0.1.0, statically checked only. It has never been run on Linux.** Shell and Python were checked with `shellcheck`, `bash -n` and
> `py_compile`, and reviewed by reading; the tests and the distro matrix are written but unrun, so expect defects on first contact.
> Everything below describes intended behaviour. Details: [docs/OUTCOME.md](docs/OUTCOME.md) section 5 and [docs/VERIFICATION.md](docs/VERIFICATION.md).
> No license is set yet (the owner's choice before publishing).

A Linux installer and configuration overlay for the [c101](https://github.com/rustcircle/c101) Claude Code agent stack
(64 agents, 239 skills, 9 workflows, hooks, MCP servers, self-tuning), targeting **Claude Desktop on Linux (its Code
tab)** and the **Claude Code CLI**.

c101 is not forked or patched. This repository locates a c101 checkout (your own, a sibling `../c101`, or a clone),
runs c101's own installer with flags chosen by a profile, and adds the Linux layer around it:

| Layer | What it adds |
|---|---|
| Source | `--source PATH`, `$C101_SOURCE`, sibling `../c101`, or a clone of `https://github.com/rustcircle/c101.git` |
| Prerequisites | uv (astral's installer, downloaded to a file, never piped), Node >= 18 from the distro, git/curl |
| Claude clients | Claude Desktop from Anthropic's signed APT repo (fingerprint pinned), Claude Code CLI from the signed apt repo or Anthropic's installer; community builds are described, never fetched |
| PATH for GUI apps | one managed block in the login-shell profile the user's shell reads, plus `~/.config/environment.d/60-c101-linux.conf` on systemd sessions |
| Containers | **rootless Podman** instead of Docker: packages, subuid/subgid, a wrapper that fixes c101's sandbox under rootless uid mapping, `DESKTOP_SANDBOX_RUNTIME`, systemd user units for the Docker-compatible API socket, image refresh and prune, optional `docker` shim |
| Desktop config | opt-in merge of curated `c101-*` MCP servers into `claude_desktop_config.json` (backup, refuses while the app runs) |
| Safety | idempotent, dry-run, timestamped backups of every user file it edits, state file so the uninstaller removes only what was added |

Documentation: [scenarios and the feature parity table](docs/SCENARIOS.md) | [pitfalls](docs/PITFALLS.md) |
[changes that belong upstream](docs/UPSTREAM.md) | [outcome](docs/OUTCOME.md)

## Quick start

Needs Linux, bash >= 4, and a normal user (the installer refuses root unless `--allow-root`; Claude Desktop refuses
root too). `sudo` is used only for package installs and system files; `--no-sudo` prints those commands instead.

```bash
git clone <this repo> c101Linux && cd c101Linux
# c101 itself: put a checkout next to this repo (../c101), or pass --source, or let the installer clone it.

./install-linux.sh --dry-run                      # see what would happen; changes nothing
./install-linux.sh                                # profile "standard"
bin/c101-linux-doctor                             # read-only health check, then c101's own doctor
```

### Per scenario

| You have | Run |
|---|---|
| Ubuntu 22.04+/Debian 12+ desktop, nothing installed | `./install-linux.sh --profile full --install-desktop --install-cli` |
| Same, Desktop already installed | `./install-linux.sh --profile standard` (then quit and restart Desktop once: it reads PATH at launch) |
| Fedora / RHEL / openSUSE / Arch desktop | `./install-linux.sh --install-cli` (the Desktop checks of the default profile print the community Desktop options; nothing is fetched) |
| Server, SSH box, WSL, no Desktop | `./install-linux.sh --profile headless --install-cli --enable-linger` |
| CI or a container | `./install-linux.sh --profile ci --allow-root --no-sudo` (or as a created user) |
| Smallest footprint | `./install-linux.sh --profile minimal` (c101 core pack only, no profile files) |
| Everything c101 can do on Linux | `./install-linux.sh --profile full` |
| Also expose servers in Desktop's Chat tab | add `--desktop-chat-mcp` (read the warning below first) |
| Exact model allowlist for all users | add `--managed-settings` (writes `/etc/claude-code/managed-settings.json`) |
| NixOS | needs `programs.nix-ld.enable = true` and nixpkgs' `uv` first: the installer checks, prints the module snippet and stops otherwise ([SCENARIOS](docs/SCENARIOS.md)) |
| Silverblue / SteamOS | see [SCENARIOS](docs/SCENARIOS.md): packages are printed, not layered |

API keys go in `~/.claude/stack.env` (created by c101, mode 0600). This overlay never reads or writes it.

## Profiles (`config/profiles/*.conf`)

| Profile | c101 flags | Overlay steps |
|---|---|---|
| `minimal` | `--user --packs core` | no profile files, Desktop checks only |
| `standard` (default) | `--user --main-agent` | + login-profile PATH block, environment.d on systemd sessions, `~/.local/bin` command links, Desktop checks |
| `full` | `--user --main-agent --enforce-models` (all packs) | standard + rootless Podman, desktop-sandbox image, Podman systemd units, Playwright system libraries |
| `headless` | `--user --main-agent` | PATH block for SSH/login shells, command links, no Desktop checks, no environment.d |
| `ci` | `--user --packs core` | `--yes --no-sudo`, no Desktop checks, no profile files |

Command-line flags override a profile. `./install-linux.sh --profile full --print-config` prints the resolved plan.

## Flags

| Flag | Meaning |
|---|---|
| `--profile NAME` | `minimal`, `standard` (default), `full`, `headless`, `ci` |
| `--source PATH` / `--repo URL` / `--ref REF` | the c101 checkout, or what to clone (default origin `https://github.com/rustcircle/c101.git`, ref `main`, into `~/.local/share/c101-linux/c101`) |
| `--packs a,b` | c101 install packs, passed through (`core` always included) |
| `--mcp a,b` | optional servers from c101's `config/mcp.optional.json`, passed through; Adobe and `peekaboo` are refused on Linux |
| `--main-agent` / `--no-main-agent`, `--enforce-models` / `--no-enforce-models` | c101 flags |
| `--managed-settings` | `sudo bin/stack managed-settings --install` through c101 (exact model allowlist, machine-wide) |
| `--link-bins` / `--no-link-bins` | `~/.local/bin/{claude-stack,stack-sdk,c101-stack}` |
| `--install-desktop` | Claude Desktop from the official signed APT repo (Debian/Ubuntu only; elsewhere guidance) |
| `--install-cli` | Claude Code CLI: signed apt repo on Debian/Ubuntu, otherwise Anthropic's installer (downloaded to a file, inspected, run); skipped when `claude` exists |
| `--desktop-chat-mcp`, `--force` | merge `config/desktop-mcp.json` into `claude_desktop_config.json`; `--force` allows it while Desktop runs |
| `--node auto\|distro\|skip` | Node >= 18 for the Playwright MCP server (distro package; too old prints a fix, never a piped installer) |
| `--sandbox` | build c101's desktop-sandbox image |
| `--container-runtime podman\|docker` | default `podman`; docker is a documented fallback only |
| `--podman-services` / `--no-podman-services`, `--podman-timers` | systemd user units (see below); `full` enables them, `--no-podman-services` turns that off; timers enable the weekly image refresh and prune |
| `--docker-shim` | install the distro's `podman-docker` (a `docker` command that runs podman); refused next to a real Docker |
| `--docker-host-podman` / `--no-docker-host-podman` | export `DOCKER_HOST` (+ `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`) pointing at Podman's socket. Default `auto`: only when the API unit is enabled and no real Docker is in use (daemon socket or service, non-default `docker context`, or your own `DOCKER_HOST`); never overrides a value you set |
| `--enable-linger` | `loginctl enable-linger` so user units run without a login session |
| `--no-podman-wrapper` | use plain `podman` as the sandbox runtime instead of `~/.local/bin/c101-podman` |
| `--playwright-deps` | Chromium's system libraries through Playwright's own installer (Debian/Ubuntu) |
| `--path-integration` / `--no-path-integration`, `--environment-d` / `--no-environment-d` | PATH files |
| `--no-sudo`, `-y/--yes`, `-n/--dry-run`, `--allow-root` | behaviour. Under `--no-sudo` (or without sudo) every root command is printed **and** written to `~/.local/state/c101-linux/pending/pending-root-commands.sh`, with the files it needs next to it, so you can review it and run `sudo sh` on it after the installer has exited |
| `--adobe`, `--no-adobe` | **refused**: Adobe apps do not exist on Linux (see parity table) |

Exit codes: 0 ok, 1 failure, 2 usage, 3 refused (not Linux, or root without `--allow-root`).

`--dry-run` changes nothing. It also reaches c101: when the checkout already has a `.venv`, c101's own entry point
(`python -m stack.install --dry-run`, exactly what `install.sh` execs) runs from it; otherwise the command is printed.
c101's `install.sh --dry-run` itself would create `.venv` and the uv cache, which a dry run must not do.

## Podman instead of Docker

Docker is a root daemon plus an API socket that tools expect. Podman is daemonless and rootless, so the overlay
reproduces what Docker users rely on:

| Docker provides | Overlay provides |
|---|---|
| a daemon with `docker.sock` | systemd user socket: the distro's `podman.socket` when it exists, else `c101-podman-api.socket` + `.service`; `DOCKER_HOST` and `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE` exported from the login-profile block and environment.d (`${DOCKER_HOST:-...}`, so a value you set always wins) only when no real Docker is in use, or when you ask with `--docker-host-podman` |
| the `docker` command | optional `--docker-shim` (`podman-docker` package) |
| daemon-managed image housekeeping | `c101-sandbox-image.service` (+ weekly timer) and `c101-podman-prune.service` (+ weekly timer, never `--volumes`) |
| daemon survives logout | `--enable-linger` |
| group-based access | rootless: subuid/subgid ranges checked and added, `XDG_RUNTIME_DIR` checked |

Units live in [`systemd/user/`](systemd/user) and are installed to `~/.config/systemd/user/` by `--profile full` or
`--podman-services` (removed by the uninstaller):

| Unit | What it does |
|---|---|
| `c101-podman-api.socket` / `.service` | fallback Docker-compatible API at `%t/podman/podman.sock` (`podman system service --time=0`), installed only when the distro has no `podman.socket` |
| `c101-sandbox-image.service` | `Type=oneshot`: `desktop-sandbox --build --profile recommended --base docker.io/library/debian:bookworm-slim` with `DESKTOP_SANDBOX_RUNTIME` pointing at podman |
| `c101-sandbox-image.timer` | weekly refresh of that image (enabled with `--podman-timers`) |
| `c101-podman-prune.service` / `.timer` | weekly, scoped to c101: `podman container prune -f --filter label=dev.c101.desktop-sandbox=1 --filter until=168h` and `podman image prune -f --filter label=dev.c101.desktop-sandbox.packages`. Your other stopped containers, images, networks and all volumes are untouched (enabled with `--podman-timers`) |

Podman is the default sandbox runtime in every profile that writes the login-profile block (`standard`, `full`, `headless`): the installer puts the wrapper in
place and exports `DESKTOP_SANDBOX_RUNTIME` even when the profile itself does not install podman (`--sandbox` or `full` do). `minimal` and `ci` write no profile files by
design, so they leave c101's own runtime autodetection (docker first) alone; add `--path-integration` or `--sandbox` to get the podman default there. Docker is used only
with `--container-runtime docker`.

The sandbox runtime is `~/.local/bin/c101-podman`, a small wrapper around `podman` selected through
`DESKTOP_SANDBOX_RUNTIME` (c101 reads it before `stack.env`, so `stack.env` stays untouched). It adds
`--userns=keep-id` to `podman run` (rootless podman otherwise maps your uid to container root and c101's sandbox, which
runs as your uid on a 0700 directory, cannot write) and, on SELinux hosts in enforcing mode, turns label separation off
for that container (opt out: `echo keep > ~/.config/c101-linux/podman-selinux`).
Rootless limits (`--cpus`, `--memory`, `--pids-limit`) need cgroup v2 controllers delegated to your user manager; Ubuntu 22.04 delegates only
`memory pids`, so the installer offers `/etc/systemd/system/user@.service.d/delegate.conf` (`Delegate=cpu cpuset io memory pids`) and the doctor FAILs without it. Rationale and the upstream fix: [UPSTREAM](docs/UPSTREAM.md), [PITFALLS](docs/PITFALLS.md).
Without systemd (Alpine/OpenRC, Void, WSL without systemd, containers) the units are skipped and the manual command is printed:
`podman system service --time=0 "unix://$XDG_RUNTIME_DIR/podman/podman.sock" &`.

## Claude Desktop notes

* The Code tab has Claude Code built in; a separate `claude` CLI is optional. c101 only edits `~/.claude`, which the
  Code tab shares (settings, hooks, skills). Whether it also loads `~/.claude/agents` is not confirmed by Anthropic's docs.
* **Why the stack's MCP servers are not put into `claude_desktop_config.json` by default:** the Code tab loads that file
  too, so they would start eagerly in every Code session instead of lazily inside the agents that need them (c101's
  design), and `neural-memory` would get a second SQLite writer. `--desktop-chat-mcp` is the opt-in, with
  `c101-fetch`, `c101-playwright`, `c101-image`, `c101-desktop-sandbox` (stdio only; the HTTP servers have no place in that file
  and `neural-memory` is left out on purpose). Keys stay in `~/.claude/stack.env` through `bin/mcp-run`.
* Desktop reads the login shell's PATH once at start: restart it after the installer changed the profile.
* Only signed Anthropic sources are installed automatically: the apt key fingerprint
  `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE` is pinned and verified (gpg, isolated keyring) before a sources entry is written.

## Uninstall

```bash
./uninstall-linux.sh --dry-run           # what would go
./uninstall-linux.sh                     # removes only what the state file recorded
./uninstall-linux.sh --purge-apt-repo    # also the apt sources/keyrings this overlay wrote
./uninstall-linux.sh --purge-clone       # also the clone it made
./uninstall-linux.sh --keep-c101         # leave c101's own settings, hooks and links alone
```

It removes the PATH block and environment.d file, `c101-*` Desktop entries that still equal what was added, the
systemd units and installed commands, the managed-settings keys, calls c101's `--no-main-agent`, `--no-enforce-models`
and `--no-python-default` (only for what was enabled), strips c101's hooks from `~/.claude/settings.json` (otherwise
they would fail on every session once the link is gone), removes the `settings.json` env keys c101 added (compared with a snapshot taken
before c101 first ran), runs `claude mcp remove --scope user` for servers registered through `--mcp`, removes the
cgroup-delegation drop-in it wrote, removes the `pending/` leftovers of earlier deferred root commands (a new `pending-root-commands.sh` appears only when this uninstall itself had to defer something), and removes `~/.claude` symlinks that point into the c101 checkout. It leaves `~/.claude/stack.env`, `~/.claude/stack-data`, uv, Node, packages, the container image and linger,
and prints the commands for those.

## Doctor

`bin/c101-linux-doctor` prints `PASS/WARN/FAIL` lines (uv and Python 3.13, node/npx, claude CLI or Desktop, the
`~/.claude/stack` link, settings hooks, the login-profile block and whether a *fresh login shell* actually finds `uv`,
Desktop config JSON, keyring, podman version/rootless/subuid/storage driver, the sandbox image, the Podman API socket,
KVM, Wayland notes), then runs c101's `bin/stack doctor`. Exit 1 on any FAIL.

## Tests (run on Linux; nothing here is meant to run on macOS)

```bash
tests/run-tests.sh                 # shellcheck, bash -n, py_compile, then the unit tests (uv's Python 3.13 when uv exists)
tests/run-tests.sh -k pathblock    # a subset
tests/matrix.sh                    # distro matrix in containers, rootless podman first
CONTAINER_RUNTIME=docker tests/matrix.sh ubuntu:24.04      # docker only when asked for
```

The unit tests build a throw-away HOME and TMPDIR and cover: profile resolution, PATH block idempotence (twice -> one
block), per-shell target files, nvm resolution, JSON merge idempotence/backup/invalid-JSON/running-app refusal (a mock
`pgrep`), dry run changing nothing for every profile (also with a fake `podman` that records any call: a dry run and
the doctor must never initialise podman storage), a dry run on a host without Python, symlinked dotfiles (the link survives,
read-only targets are refused), deferred root commands that outlive the run, root refusal, the Adobe refusal, uninstall
(links, hooks, env keys, MCP servers, system files, preexisting managed keys), the `c101-podman` wrapper, the systemd unit
files (sections, keys, specifiers, install targets, label-scoped prune), cgroup delegation, the DOCKER_HOST decision, the
NixOS preflight and distro detection from os-release fixtures. The matrix runs `ubuntu:24.04`, `ubuntu:22.04`, `debian:12`, `fedora:41`, `archlinux:latest` (skipped cleanly on
arm64) and `alpine:3.20`: install, install again, doctor, a dry run that must change nothing, uninstall. It mounts both
repositories read-only and works on copies, because c101's installer needs a writable tree.

Test hooks (environment variables; not for normal use): `C101L_PY` (helper interpreter), `C101L_STATE_FILE`,
`C101_LINUX_FAKE_EUID` (root-refusal test as a normal user), `C101_LINUX_OS_RELEASE`, `C101_LINUX_UNAME_M`,
`C101_LINUX_LOGIN_SHELL`, `C101_LINUX_DESKTOP_PATTERN`, `C101_LINUX_OSTREE_BOOTED` (0/1), `C101_LINUX_CGROUP_FILE`,
`C101_LINUX_DOCKER_SOCKETS`, `C101L_SKIP_NIXOS_CHECK=1` (continue on NixOS without the nix-ld/uv preflight), `C101L_UV_VERSION`.

## Layout

```
install-linux.sh        entry point (flags, profiles, step order)
uninstall-linux.sh      reverse of the above, driven by the state file
bin/c101-linux-doctor   read-only health check
bin/c101-podman         podman wrapper installed as ~/.local/bin/c101-podman
config/profiles/*.conf  profile -> c101 flags and overlay steps
config/desktop-mcp.json curated server set for --desktop-chat-mcp
lib/*.sh                detect, deps (uv, node, podman), c101 (fetch/run/links), desktop, pathprofile, services, profiles, common
lib/*.py                stdlib-only helpers (state, managed blocks, Desktop JSON merge, settings edits)
systemd/user/*          Podman API socket, sandbox image refresh, prune units and timers
tests/                  unit tests, run-tests.sh, matrix.sh, os-release fixtures
docs/                   SCENARIOS, PITFALLS, UPSTREAM, OUTCOME
```

State lives in `~/.local/state/c101-linux/state.json` (mode 0600): profile, c101 source and flags, edited files,
installed units, apt files, Desktop entries added. Every edit of an existing user file writes `<file>.bak-<UTC stamp>` first.
