# Outcome

What c101Linux is, where it is strong, where it is not, and how far it has been verified. Dated 2026-11-14.
Read this together with [PITFALLS](PITFALLS.md), [SCENARIOS](SCENARIOS.md) and [UPSTREAM](UPSTREAM.md).

## 1. What was built

A Linux installer and configuration overlay for the c101 agent stack. It consumes c101 unmodified (your checkout, a sibling `../c101`, or a clone of
`https://github.com/rustcircle/c101.git`), runs c101's own installer with flags taken from a profile, and adds the Linux and Claude Desktop layer around it.

| Piece | Where | What it does |
|---|---|---|
| Installer | `install-linux.sh`, `lib/*.sh`, `config/profiles/*.conf` | five profiles (minimal, standard, full, headless, ci), about 40 flags, `--dry-run`, never as root, `sudo` only for packages and system files, `--no-sudo` prints instead |
| Uninstaller | `uninstall-linux.sh` | reverses only what the state file recorded, plus c101's own reversible flags, hooks, env keys, `--mcp` servers |
| Doctor | `bin/c101-linux-doctor` | read-only PASS/WARN/FAIL checks, then c101's `bin/stack doctor` |
| Helpers | `lib/*.py` (stdlib only) | state file, idempotent managed blocks, Desktop JSON merge, settings.json edits; all write with a timestamped backup, through symlinks |
| Podman layer | `bin/c101-podman`, `systemd/user/*`, `lib/deps.sh`, `lib/services.sh` | rootless podman instead of Docker for c101's sandbox: wrapper, API socket unit, image refresh and prune units, optional `docker` shim |
| Claude Desktop | `lib/desktop.sh`, `config/desktop-mcp.json` | official signed APT repo (fingerprint pinned), guidance for everything else, opt-in server merge into `claude_desktop_config.json` |
| Tests | `tests/` (8 test modules, 102 test functions, `run-tests.sh`, `matrix.sh`) | written for Linux, not yet run (section 5) |
| Docs | `README.md`, `docs/` | usage, 42 pitfalls with symptom/cause/fix, 12 upstream change proposals with file:line |

About 3.8k lines of shell and Python, 1.6k lines of tests, 6 systemd units.

## 2. Feature parity

The full table (c101 on macOS versus c101Linux, with alternatives) is in [SCENARIOS.md](SCENARIOS.md#1-feature-parity-c101-on-macos-vs-c101linux).
In short: `--profile full` enables everything c101 can do on Linux (all packs, `--user`, delegator main agent, tier-model enforcement, hooks, the learning loop,
`bin/stack-sdk`, `bin/claude-stack`, all nine MCP servers, `--mcp` passthrough, `--managed-settings`). Not possible on Linux, and refused or documented as such:
Adobe servers (`--adobe` exits 2), the built-in macOS-only `computer-use` server and `peekaboo` (the `desktop-sandbox` container desktop is the Linux route),
Computer Use and Dictation inside Claude Desktop's Linux build.

## 3. Where it shines

These are the design strengths and the intended behaviour. None of the runtime behaviour below has been executed yet (section 5); read every "does" as "is written to".

* **Official, verified Desktop on Debian/Ubuntu.** The Claude Desktop beta ships as a signed APT repository. The installer downloads the key, checks the primary
  fingerprint `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE` with gpg in an isolated keyring, and only then writes the sources entry (`lib/desktop.sh`). It will not
  install a community build; for other distributions it prints options instead of fetching anything.
* **One `~/.claude` for the Code tab and the CLI.** c101 only edits `~/.claude` (agents, skills, hooks, settings), which Desktop's Code tab shares with the CLI, so a single install serves both.
* **The GUI PATH problem is addressed where it occurs.** A GUI-launched Desktop does not read `~/.bashrc`. One managed block in the login-shell profile the user's shell really reads
  (plus `environment.d` on systemd sessions) covers uv, node, npx, nvm, volta, asdf, mise, linuxbrew, snap and cargo. The doctor is written to prove it with a clean login shell.
* **Rootless Podman with the pieces Docker users expect.** Subuid/subgid ranges are checked and added, the cgroup-controller delegation Ubuntu 22.04 lacks is detected and fixed,
  a wrapper fixes c101's uid mapping under rootless podman, a systemd user socket replaces the daemon (`DOCKER_HOST` only when no real Docker is in use), timers refresh the
  sandbox image and prune only c101's leftovers, `--docker-shim` keeps the `docker` command working. All opt-in through the `full` profile or flags.
* **Safe to run twice and to take back.** Every step is idempotent, every edit of an existing file makes `<file>.bak-<UTC stamp>` first (symlinked dotfiles are edited through the link),
  `--dry-run` changes nothing (also with podman installed, and on a host without Python), and the uninstaller works from a state file, so it removes only what was added and prints
  the commands for the rest.
* **Profiles instead of a wall of flags.** minimal, standard, full, headless and ci cover a laptop, a workstation, an SSH box and a pipeline; every flag overrides a profile.
* **Honest limits.** Root commands that cannot run (`--no-sudo`) are written to a reviewable script with the files they need; things that were reasoned but not observed are marked *unverified*
  in the pitfalls.

## 4. Where it struggles

| Limit | Why | Detail |
|---|---|---|
| Desktop is community-only outside Debian/Ubuntu | Anthropic ships a `.deb` repo only; Fedora/RHEL/Arch/NixOS rely on community repackaging, which the overlay will not fetch | [SCENARIOS](SCENARIOS.md#2-scenario-matrix) |
| No built-in computer use | c101's `computer-use` server and Claude Desktop's Computer Use are macOS/Windows only | the container desktop (`desktop-sandbox`) is the substitute; it cannot see your own apps |
| NixOS | uv and the Python builds it downloads cannot run without `programs.nix-ld`; the overlay stops with a module snippet instead of installing | [PITFALLS 41](PITFALLS.md#41-nixos-nixos-cannot-run-dynamically-linked-executables-while-installing) |
| cgroup delegation | rootless limits need `cpu` delegated to the user manager; Ubuntu 22.04 does not, and fixing it needs root and a re-login | [PITFALLS 33](PITFALLS.md#33-every-sandbox_start-fails-on-ubuntu-2204-the-requested-cgroup-controller-cpu-is-not-available) |
| GUI PATH needs a restart | Desktop reads PATH once at launch; a stale nvm directory needs a re-run | PITFALLS 1 to 3 |
| Keyring | Desktop cannot keep its sign-in without GNOME Keyring or KWallet; the installer warns, it does not install one | PITFALLS 8 |
| Agents in the Code tab | Anthropic's docs do not say whether `~/.claude/agents` is loaded there (skills, hooks and settings are) | PITFALLS 15 |
| Sandbox under rootless podman | c101 runs the container as your uid on a 0700 directory and mounts without SELinux labels; the wrapper works around it, an upstream fix is the real answer | PITFALLS 28/29, UPSTREAM 4 |
| Immutable and exotic systems | Silverblue, SteamOS, Alpine/musl, WSL get guidance, not an install | SCENARIOS |
| Not all of c101's installer is idempotent-friendly | its `install.sh --dry-run` still creates `.venv`; there is no uninstall | UPSTREAM 3 and 8 |

## 5. Verification status (honest)

* **Executed on Linux: nothing, ever.** The build machine is a Mac (nix) and the target is Linux. Four read-only invocations of `install-linux.sh` ran on the Mac *before* the instruction not to
  test there: `--help`, `--print-config` twice (once under `bash -x`), and one `--dry-run`, which exited 3 at the Linux-only gate. They wrote nothing. Since that instruction only static checks ran there
  (shellcheck, `bash -n`, `py_compile` into a scratch cache, `jq`). Nothing else was run on the Mac: no uninstaller, doctor, test or container, and no c101 code.
* **Static checks only**, all green on the final tree: `shellcheck -x` on 14 shell files (exit 0), `bash -n` on the same files, `py_compile` with Python 3.13 on 13 Python files, `jq` on the one JSON config.
* **Independent review** (summary and finding-to-fix table: [VERIFICATION.md](VERIFICATION.md)): verdict "request changes", 3 high, 6 medium, 6 low, 3 nits and one refuted candidate. All were
  addressed in code; **the fixes were not re-reviewed and have not run** (the later quality gate only confirmed that they are present, not that they are correct). The three high findings:
  dry run/doctor initialised podman storage (H1), the sandbox could not start on Ubuntu 22.04 for lack of `cpu` delegation (H2), the install aborted on stock NixOS (H3).
* **Quality gate and fact-check:** a quality gate (verdict FIX-FIRST, since addressed) and a fact-check of the Desktop installation facts against Anthropic's documentation are summarised in [VERIFICATION.md](VERIFICATION.md).
* **Not verified at all:** the behaviour of every shell function and Python helper at runtime, the 102 written tests (they have never run, so some will probably fail on first contact),
  the distro matrix, the live apt key fingerprint, `c101-podman` under real rootless podman and SELinux, the systemd units under `systemd --user`, Desktop on Ubuntu 24.04 including AppArmor,
  and whether c101's Python wheels exist for Alpine.

## 6. How to verify on Linux

```bash
tests/run-tests.sh                       # shellcheck, bash -n, py_compile, then all unit tests (temp HOME, no network)
tests/run-tests.sh -k uninstall          # a subset
tests/matrix.sh                          # rootless podman, 6 distributions: install, install again, doctor, dry run diff, uninstall
CONTAINER_RUNTIME=docker tests/matrix.sh ubuntu:24.04    # docker only when asked for
./install-linux.sh --dry-run --profile full              # read-only plan on your own machine
bin/c101-linux-doctor                                    # after a real install
```
The first thing to run is `tests/run-tests.sh`; failures there are most likely test or shell defects rather than environment problems. Then the matrix on a rootless-podman host
(SELinux Fedora and an Ubuntu 22.04 VM are the interesting ones: they exercise the wrapper's SELinux branch and the cgroup delegation fix).

## 7. Open questions

1. Does the official Desktop Code tab load `~/.claude/agents`? Test: a Code session, `/agents`. (PITFALLS 15)
2. Does the official `.deb` ship an AppArmor profile for Ubuntu 23.10+/24.04, and does its postinst register an APT entry with a different `signed-by` path? (PITFALLS 10, 14)
3. Is the pinned key fingerprint still the one served by `https://downloads.claude.ai/claude-desktop/key.asc` and `.../keys/claude-code.asc`? (the installer aborts safely if not)
4. Do `--userns=keep-id` and `--security-opt label=disable` fully fix the sandbox under rootless podman, or does c101 need the upstream change in UPSTREAM 4?
5. Which directory does Claude Desktop use as the working directory for MCP servers? `config/desktop-mcp.json` sets `CLAUDE_PROJECT_DIR` for the sandbox entry to be safe.
6. Do all of c101's Python dependencies resolve on Alpine/musl and on arm64 Linux?
7. For dnf, zypper, apk and pacman the Claude Code CLI comes from Anthropic's installer script, because no exact signed repository lines were pinned in the research notes. Are signed rpm/apk repositories worth adding?
8. Should `--dry-run` call c101's `install.sh --dry-run` literally? It currently runs c101's `python -m stack.install --dry-run` from an existing `.venv` (or prints the command), because the former creates `.venv`.
