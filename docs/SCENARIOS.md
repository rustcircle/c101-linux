# Scenarios and feature parity

What `install-linux.sh` does on each kind of system, and how c101's features map onto Linux. Statements about
Anthropic's software come from the research notes (code.claude.com/docs/en/desktop-linux, /desktop, /setup;
accessed 2026-11-14). Statements marked *unverified* were reasoned from the docs or the code, not observed on a machine.

## 1. Feature parity: c101 on macOS vs c101Linux

`--profile full` enables every row marked "yes". N/A rows are things that cannot exist on Linux.

| Feature | c101 on macOS | c101Linux | Notes and alternative |
|---|---|---|---|
| All install packs, agents, skills, workflows (`--user`) | yes | yes | c101's own installer does the linking; `--packs a,b` passes through |
| Delegator as main agent (`--main-agent`) | yes | yes | profile `standard` and up |
| Tier-model enforcement (`--enforce-models`) | yes | yes | profile `full`; prefix matching in user settings |
| Exact model allowlist for every user (`sudo bin/stack managed-settings --install`) | yes (`/Library/Application Support/ClaudeCode`) | yes, behind `--managed-settings` | `/etc/claude-code/managed-settings.json`; run with `PYTHONDONTWRITEBYTECODE=1` so root leaves no `__pycache__` in your checkout; removed by the uninstaller (only the keys it wrote) |
| Hooks (session start, pre/post tool, model switch, ...) | yes | yes | merged into `~/.claude/settings.json` by c101; the Code tab shares that file |
| Self-tuning (`bin/stack learn ingest/update/apply`) | yes | yes | driven by the SessionEnd hook, same as macOS; nothing to schedule |
| SDK runner (`bin/stack-sdk run/agents/check`) | yes | yes | `stack-sdk` is linked into `~/.local/bin`; doctor (profile full) runs `stack-sdk check` (no API call) |
| `bin/claude-stack` (delegator pinned to its tier model) | yes | yes | needs the `claude` CLI (`--install-cli`); Desktop-only installs do not have it |
| MCP `playwright` | yes | yes | needs Node >= 18 + npx; `--playwright-deps` installs Chromium's libraries on Debian/Ubuntu |
| MCP `exa`, `jina`, `spider`, `context7` (HTTP) | yes | yes | lazy per agent; keys in `~/.claude/stack.env` |
| MCP `fetch`, `image`, `neural-memory` (stdio) | yes | yes | via `bin/mcp-run` / `bin/neural-memory-mcp` (uvx, pinned) |
| MCP `desktop-sandbox` (container desktop) | yes (docker or podman) | yes, **podman default** | rootless podman, wrapper, image build; docker only with `--container-runtime docker` |
| Optional servers (`--mcp a,b`) | yes | yes | passed to c101; registered at user scope through the `claude` CLI (without it c101 prints the manual command) |
| Built-in `computer-use` server (drives your own macOS apps) | yes (interactive, Pro/Max) | **N/A** | macOS-only upstream. Use `desktop-sandbox` (a disposable, network-less Linux desktop with LibreOffice etc.); the `computer-operator` agent already falls back to it |
| `peekaboo` optional server | yes | **N/A** (refused by `--mcp`) | drives the macOS desktop |
| Adobe servers (`--adobe`: InDesign, Illustrator, Photoshop, After Effects, Premiere Pro) | yes | **N/A**, `--adobe` is refused with this explanation | the apps exist for macOS and Windows only; c101 itself skips Adobe setup off-darwin. For vector/raster work on Linux use the `image` server and the sandbox (Inkscape, GIMP/Krita via `bin/desktop-sandbox --profile recommended --packages "krita inkscape"`) |
| GUI-launched app finds uv/node/npx/podman | n/a (macOS login env differs) | yes | login-profile block + environment.d (c101's launchers hard-code only macOS/nix paths) |
| Claude Desktop install | cask/dmg | official signed `.deb` repo on Debian 12+/Ubuntu 22.04+ | other distros: guidance only |
| Cowork (VM) | yes | needs `/dev/kvm` + kvm group | doctor reports it; not needed for Chat or Code |
| Computer Use / Dictation in Desktop | yes | **N/A** in the Linux build | Anthropic: not available on Linux yet |

## 2. Scenario matrix

Desktop route: **official** = Anthropic's signed APT repo (`--install-desktop`); **community** = AUR / aaddrick /
claude-desktop-extra, printed as guidance, never fetched; **none** = CLI only.

| System | Desktop | CLI (`--install-cli`) | What the installer does |
|---|---|---|---|
| Ubuntu 24.04 / 22.04, Debian 12 (amd64, arm64) | official | signed apt repo | everything: prerequisites, uv, Node from distro (22.04 has Node 12: prints the version-manager fix), c101, PATH block + environment.d, podman layer in `full`. Ubuntu 23.10+/24.04 restrict unprivileged user namespaces (AppArmor): noted by the checks |
| Linux Mint, Pop!_OS, other Debian derivatives | official, "may work" (Anthropic lists Ubuntu/Debian only) | signed apt repo | same, with a warning |
| Debian 11, Ubuntu 20.04, anything with glibc < 2.34 | none (needs libc6 >= 2.34) | official installer script (Ubuntu 20.04+/Debian 10+ supported by the CLI) | Desktop step prints why; rest works |
| Fedora, RHEL, Rocky, Alma, openSUSE | community only (Anthropic: "coming in the future") | official installer script | the Desktop checks (profiles standard, full, minimal) print the community options without any flag; podman via dnf/zypper; SELinux notes; PATH block |
| Arch, Manjaro, EndeavourOS | community: AUR `claude-desktop` (repackages the official build) | official installer script | the Desktop checks print the AUR hint; podman via pacman |
| NixOS | community flake | official installer script (needs nix-ld, see below) | **preflight first**: stock NixOS cannot run astral's uv or the Python builds uv downloads, so the installer does not fetch uv there; it requires nixpkgs' `uv` and `programs.nix-ld.enable = true` and otherwise prints the module snippet (nix-ld, `uv`, `virtualisation.podman` with `dockerCompat`) and stops (`C101L_SKIP_NIXOS_CHECK=1` overrides). Packages are never installed imperatively; the PATH block is written (a home-manager symlink to `/nix/store` is refused with the block printed) |
| Fedora Silverblue/Kinoite/Bazzite (ostree) | none directly | official installer script (user space) | no package layering: prints `rpm-ostree install` / toolbox / distrobox guidance. Podman is already part of these images. A distrobox with Ubuntu 24.04 can host the official Desktop (*unverified here*) |
| SteamOS (Steam Deck) | none directly | official installer script | read-only root: prints distrobox or `steamos-readonly disable` guidance |
| Alpine (musl) | none (needs glibc) | official installer script (needs bash + curl, installed first) | rootless podman via apk (`shadow-uidmap`); no systemd: units skipped, manual `podman system service` printed. c101's Python wheels on musllinux are *unverified* (matrix result in OUTCOME) |
| Void, Gentoo, other | none | official installer script | prints guidance; no package manager actions |
| WSL2 | not recommended: use the Windows Desktop app | official installer script | profile `headless`; systemd units only when WSL has `systemd=true`; podman in WSL needs the usual rootless setup |
| Docker/Podman container, CI | none | installer script | profile `ci` (`--yes --no-sudo`), `--allow-root` for a root container |
| SSH-only server | none | signed apt repo or installer script | profile `headless`; PATH block for login shells; `--enable-linger` keeps user units alive after logout |
| Ubuntu 22.04 (systemd 249) + rootless podman | official | signed apt repo | the user manager delegates `memory pids` but not `cpu`, so c101's sandbox (`--cpus`) cannot start: the installer offers the `user@.service.d/delegate.conf` drop-in (sudo, or printed with `--no-sudo`), the doctor FAILs until it is active |
| Docker already in daily use (docker-ce) | any | any | `DOCKER_HOST` is not pointed at Podman (that would hide your containers); `--docker-host-podman` forces it |
| NFS-mounted home | works, with caveats | CLI warns about Bus errors on shared NFS homes | rootless podman storage on NFS falls back to `vfs` (doctor warns); see PITFALLS |

## 3. What each profile touches

| Path | minimal | standard | full | headless | ci |
|---|---|---|---|---|---|
| `~/.claude/*` (via c101) | core pack | all packs | all packs + model enforcement | all packs | core pack |
| login-profile PATH block | no | yes | yes | yes | no |
| `~/.config/environment.d/60-c101-linux.conf` | no | systemd sessions | systemd sessions | no | no |
| `~/.local/bin/{claude-stack,stack-sdk,c101-stack}` | no | yes | yes | yes | no |
| podman, `~/.local/bin/c101-podman`, sandbox image | no | no | yes | no | no |
| `~/.config/systemd/user/c101-*` | no | no | yes | no | no |
| `DESKTOP_SANDBOX_RUNTIME`, `DOCKER_HOST` exports | no | no | yes | no | no |
| Desktop checks (keyring, KVM, Wayland) | yes | yes | yes | no | no |
| system packages, apt sources | only what flags ask for | same | same | same | printed, not run (`--no-sudo`) |
