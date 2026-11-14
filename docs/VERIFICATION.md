# Verification record

What was checked, by whom, what it found and what changed. Dated 2026-11-14. Status in one line: **statically checked and reviewed by reading; never executed on Linux.**
This file summarises the review, the quality gate and the fact-check so the repository does not depend on agent working files.

## 1. Independent code review (read-only, nothing executed)

Method: every file read, callers in c101 traced, `shellcheck -x`, `bash -n` and `py_compile` run; a second pass tried to refute the uncertain findings.
Verdict: request changes. 3 high, 6 medium, 6 low, 3 nits; one candidate (spurious ERR-trap output inside command substitutions) was refuted.

| Id | Finding | Fix in this repository |
|---|---|---|
| H1 | `--dry-run` and the doctor ran `podman info`, which initialises rootless storage | `lib/deps.sh` `setup_sandbox` returns before any probe in a dry run; `lib/detect.sh` `detect_container_runtime` probes only when told to or when storage exists; doctor skips probes without storage; tests with a fake `podman` |
| H2 | the sandbox cannot start under rootless podman on Ubuntu 22.04: the `cpu` cgroup controller is not delegated (systemd 249) | `cgroup_missing_controllers`, `ensure_cgroup_delegation` (drop-in `user@.service.d/delegate.conf`), doctor FAIL, PITFALLS 33, UPSTREAM 10 |
| H3 | install aborts on stock NixOS (uv and its Python are dynamically linked) | `nixos_check` requires nix-ld and nixpkgs' uv, prints a module snippet, never fetches astral's binary there; PITFALLS 41, UPSTREAM 12 |
| M1 | `DOCKER_HOST` pointed at Podman even with a real Docker in use | `docker_in_use`, `docker_host_decision`, `--docker-host-podman`, environment.d `${VAR:-default}`; PITFALLS 37 |
| M2 | deferred (`--no-sudo`) root commands referred to deleted temp files | staged payloads and `pending-root-commands.sh` under `~/.local/state/c101-linux/pending/`; PITFALLS 38 |
| M3 | `--dry-run` died on hosts without Python | pure-bash `json_str`, plan-only branches |
| M4 | symlinked dotfiles were replaced by regular files | helpers edit through links, refuse `/nix/store` and read-only targets; PITFALLS 39 |
| M5 | uninstall left c101's `--mcp` servers and env keys | state snapshot, `strip-env`, `claude mcp remove --scope user` |
| M6 | the prune timer deleted all stopped containers | prune scoped to c101 labels; PITFALLS 40 |
| L1-L6, nits | state bookkeeping, nvm edge case, SELinux opt-out file, wrapper fallback, host-dependent test, apt updates, unit `Type=exec`/`Delegate=true` | see the "Fixes" table in the build log (not shipped; every item is visible in the code and tests) |

The fixes were **not re-reviewed**.

## 2. Quality gate (before the first commit)

Verdict: FIX-FIRST (1 blocker, 1 major, 6 minor, 4 nits). 14 of 14 explicit requirements met; 18 of 20 load-bearing claims checked (15 confirmed, 3 contradicted, 2 unverifiable without Linux).
Hygiene checks passed: `.gitignore`, no secrets (one public-fingerprint false positive), nothing copied from c101, executable bits, no absolute home paths.
The gate found and the repository now corrects:

| Id | Finding | Change |
|---|---|---|
| QC-1 | the outcome document said nothing had run on the Mac, but four read-only invocations had run before the no-testing instruction | OUTCOME section 5 states exactly that |
| QC-2 | a pitfall claimed its behaviour was already tested | replaced by "covered by tests/test_pathblock.py; not yet run" |
| QC-3 | README/SCENARIOS said `--install-cli` prints community Desktop options, the code did that only under `--install-desktop` | `desktop_prechecks` now prints them for profiles with Desktop checks |
| QC-4, QC-5 | no status line; untested behaviour phrased as fact; references to ignored working files | status banner in the README, "intended behaviour" wording, this file |
| QC-6 | podman was the default only with `--sandbox`/`full` | wrapper and `DESKTOP_SANDBOX_RUNTIME` in every profile that writes the block (standard, full, headless); minimal and ci documented |
| QC-7 | the sandbox-image unit's PATH lacked NixOS and Homebrew locations | unit PATH and wrapper search path extended |
| QC-8 | `pending/` outlived the uninstall | the uninstaller removes it |
| QC-9, QC-10, QC-12 | gitleaks false positive, undocumented `--no-podman-services`, stale unit comment | `# gitleaks:allow`, documented, fixed |
| QC-11 | no LICENSE | deliberately left to the owner; the README says so |

## 3. Fact-check of the Anthropic-side facts the installer relies on

Eight claims (twelve atomic statements) were checked against primary documentation on 2026-11-14: 11 supported, 1 supported with a source caveat, 0 contradicted.

| Fact used by the installer | Primary source |
|---|---|
| official Claude Desktop beta for Linux: `.deb` through the APT repo `https://downloads.claude.ai/claude-desktop/apt/stable stable main`, key `https://downloads.claude.ai/claude-desktop/key.asc`, fingerprint `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`, Ubuntu 22.04+/Debian 12+, amd64/arm64, libc6 >= 2.34, no Fedora/RHEL build, no Computer Use or Dictation, Cowork needs KVM, no self-update, root refused without `--no-sandbox` | https://code.claude.com/docs/en/desktop-linux |
| Claude Code CLI apt repo `https://downloads.claude.ai/claude-code/apt/stable stable main`, key `https://downloads.claude.ai/keys/claude-code.asc`, `curl -fsSL https://claude.ai/install.sh \| bash` | https://code.claude.com/docs/en/setup |
| the Code tab shares `~/.claude.json`, `~/.claude/settings.json`, hooks, skills, CLAUDE.md, `.mcp.json`; it also loads `claude_desktop_config.json` servers; the app reads the login shell's exports once at start | https://code.claude.com/docs/en/desktop |
| managed settings on Linux/WSL at `/etc/claude-code/managed-settings.json` | https://code.claude.com/docs/en/managed-settings |
| `~/.config/Claude/claude_desktop_config.json` with a top-level `mcpServers` object | https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers (an MCP-project page, not Anthropic's Desktop docs: the source caveat) |

Caveats recorded by the fact-checker: the login-shell statement is a general environment statement, not PATH-specific; whether the Code tab loads `~/.claude/agents` is not stated anywhere (so the docs mark it *unverified*).

## 4. Static checks on the final tree

`shellcheck -x` on all shell files (exit 0), `bash -n` (exit 0), `py_compile` with Python 3.13 on all Python files (exit 0), `jq empty` on `config/desktop-mcp.json` (exit 0).

## 5. What remains unverified

Everything dynamic. See [OUTCOME.md](OUTCOME.md) sections 5 to 7 for the list and for the commands to run on Linux (`tests/run-tests.sh`, `tests/matrix.sh`, `bin/c101-linux-doctor`).
