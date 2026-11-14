# Verification record

What was checked, by whom, what it found and what changed. Dated 2026-11-14. Status in one line: **checked statically and reviewed by reading, with some single functions also checked in memory on the Mac (section 6, "What ran on the Mac"); never executed on Linux.**
This file summarises the review, the quality gate and the fact-check so the repository does not depend on agent working files.
Sections 1 to 4 cover the first version. Section 6 covers the parity change of the same day (keys and fal image generation,
research servers, presets, MCP pre-warm, Playwright's Chromium, the fal-runner agent, the learning backstop). Section 5 lists
what remains unverified for both.

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

### Parity change: verify on Linux

Nothing of the parity change has run on Linux. The first three rows are the runtime doubts that no unit test can settle; the
fourth is the fal auth doubt. The Linux-only checks in `tests/test_linux_runtime.py` are skipped unless asked for:
`C101_LINUX_RUNTIME_TESTS=1 python3 -m unittest discover -s tests -p 'test_linux_runtime.py' -v` (add `-k <class name>` for one
class). Run them after `./install-linux.sh --profile standard` (or full, headless) and one logout and login. Record each result
in this file.

| # | Verify on Linux | Check |
|---|---|---|
| V1 | **environment.d reaching stdio MCP servers**: `PLAYWRIGHT_MCP_BROWSER` and `PATH` from `~/.config/environment.d/60-c101-linux.conf` are in the user manager, in a child it starts, and in a running `@playwright/mcp` that Desktop started | `UserManagerEnvironmentTests` in `tests/test_linux_runtime.py` (its last test needs Desktop running with the Playwright server connected). PITFALLS 2, 43 |
| V2 | **Chromium install command for @playwright/mcp**: `npx -y playwright-core@<pin> install --no-shell chromium` leaves the revision the MCP launches, and that build starts; `npm view @playwright/mcp@latest dependencies.playwright-core` prints a bare version; an arm64 build exists for the host | `PlaywrightChromiumTests` in `tests/test_linux_runtime.py`; doctor `playwright-browser`; on arm64, Ubuntu 22.04+ or Debian 12+. PITFALLS 43 |
| V3 | **SessionEnd on Desktop quit**, and the learning backstop that covers a missed one | `LearningBackstopRuntimeTests` (automatic: timer enabled, active and persistent; a session without SessionEnd gets exactly one history row) and `DesktopQuitManualCheck` (manual steps in the docstring of `tests/test_linux_runtime.py`), both in that file. PITFALLS 50 |
| V4 | **fal MCP key header vs OAuth (`https://mcp.fal.ai/mcp-relay`)**: do fal's tool calls accept `Authorization: Key <FAL_KEY>`, or only OAuth? | No automated test (the doctor's `fal-mcp-reach` probe sends no key on purpose). Manual, with `FAL_KEY` set (doctor `fal-runner` and `keys-image` pass): `claude --mcp-config ~/.config/c101/fal-mcp.json`, `/mcp` shows fal connected; ask for a model-catalog search (free), then let `fal-runner` run one model under its USD 0.50 limit. A 401 or 403 on the call means the header is refused: use the OAuth fallback of PITFALLS 53 and record it here |
| V5 | `lib/stackenv.py merge` write path: temp file and `os.replace`, the backup, a symlink surviving, the lock, exit 4 for `/nix/store` and read-only mounts, CRLF rewrite, mode tightening | `tests/run-tests.sh` (the unit tests of the key step). The merge write path has never run anywhere. PITFALLS 45, 54, 57 |
| V6 | No key value in any child's argv or environment, in dry-run output or in the state file | `tests/run-tests.sh` (tests with the `SECRET_SENTINEL` value) |
| V7 | Ctrl-C at the hidden prompt gives echo back on bash 4.2 and 4.3 | manual on such a host: `./install-linux.sh --keys --keys-only`, Ctrl-C at the `FAL_KEY` prompt, then type: the characters must show |
| V8 | Pre-warm: the first real start of fetch, neural-memory and Playwright no longer times out | `tests/test_prewarm.py` (arguments, with stubs); manual: a fresh session whose first agent uses fetch. PITFALLS 44 |
| V9 | Backstop selection: age window, already-ingested and stamped transcripts, the cap of 20 | `tests/test_learning_timer.py` |
| V10 | The image router shows a newly configured operation without a restart | manual: `./install-linux.sh --keys --keys-only` while a session runs, then ask `image-generator` for an image; `~/.claude/stack/bin/image-router --check`. PITFALLS 47, 56 |
| V11 | Security residuals of the helper path (S1 SEC-4): does Claude Code give a linked agent's helper `CLAUDE_PROJECT_DIR`, or the project as working directory? Do `-p --mcp-config` helpers run before the trust check? | manual, in a scratch directory holding an executable `bin/mcp-headers` that only creates a marker file: start `claude` there and use `fal-runner`; the marker must not appear. UPSTREAM 22 |
| V12 | Playwright on Fedora or Arch (generic build), and the Alpine fallback (`apk add chromium`, `PLAYWRIGHT_MCP_EXECUTABLE_PATH`) | `PlaywrightChromiumTests` on that host; doctor `playwright-browser` |

## 6. Parity change of 2026-11-14 (not run on Linux; static checks and in-memory checks on the Mac)

What changed: 11 new installer flags; `lib/stackenv.py` (names-only inspection, stdin-only merge, fal preset fill, symlink-aware
atomic write with backup); `lib/keys.sh`; presets (`config/presets.conf`, `lib/presets.sh`, `lib/packs.py`); `lib/prewarm.sh`
(MCP pre-warm and Playwright's Chromium); the learning backstop (`bin/c101-learn-backstop`, two systemd user units,
`lib/learning.sh`); the hosted fal MCP as the `fal-runner` agent and `config/fal-mcp.json` (`lib/falmcp.sh`); 22 per-feature
doctor checks (the old `hooks` id became `hooks-events`); uninstaller changes; tests (below); README, SCENARIOS (a 76-row
parity table), OUTCOME, PITFALLS 43 to 59 and UPSTREAM 13 to 26.

Tests written for the change, **none of them run** (the user forbade running the suite on the Mac; nothing runs Linux here),
counted as `def test_` functions:

| Node | Files | Tests |
|---|---|---|
| T1 | `tests/test_keys.py` 77, `tests/test_presets.py` 41 | 118 new |
| T2 | `tests/test_prewarm.py` 21 and `tests/test_learning_timer.py` 24 (45 new), `tests/test_pathblock.py` 17 to 23 (+6), `tests/test_linux_runtime.py` 13 (opt-in, `C101_LINUX_RUNTIME_TESTS=1`) | 45 + 6 + 13 |
| T3 | `tests/test_doctor_features.py` 38 (new); updated assertions in the existing modules (the old minimal and `--packs` pack lists, the help-flag list), `tests/helpers.py`, `tests/matrix.sh` (fake keys) | +63 in the tracked modules (102 to 165, T2's +6 included), +38 new, 0 removed |

In total T1 to T3 wrote 277 test functions, which brought the count to 379 in 14 modules, up from 102 in 8. T1 covers
the 17 mandatory test names of its brief; T2 flagged `tests/test_podman.py` for T3; T3 removed no test. The fix step FX1
added 13 regression tests (392), FX2 one more (393) and FX3 two (395); none removed a test. The table gives the counts as each node left them; today
(`grep -c 'def test_' tests/*.py`, 395 in all): `test_keys.py` 80, `test_presets.py` 41, `test_prewarm.py` 21,
`test_learning_timer.py` 27, `test_linux_runtime.py` 13, `test_doctor_features.py` 43, and 170 in the 8 modules of the base
commit (`test_installer.py` 38, `test_profiles.py` 25, `test_pathblock.py` 23, `test_podman.py` 23, `test_desktop_config.py`
20, `test_helpers_py.py` 19, `test_uninstall.py` 17, `test_detect.py` 5).

### Who checked what

| Check | By | Method | Result |
|---|---|---|---|
| Plan | plan-critic | read the plan and its task graph | REVISE: 0 blockers, 9 majors; patches folded into the plan before work started |
| Playwright browser decision (env variable, install command, arm64, musl) | quick-researcher, then fact-checker | Playwright source, npm registry, playwright.dev, accessed 2026-11-14 | 6 of 6 claims supported; caveats: `npm view` output format not executed, the Alpine statement only indirect |
| GATE0: `lib/stackenv.py`, presets, learning backstop, test stub | validator | `shellcheck -x`, `bash -n`, `py_compile`, `jq`, `grep`, code reading; a scratch script compared the test stub with c101's fal example by names and hosts only | PASS, all four; behaviour by code reading |
| GATE1: `lib/keys.sh`, pre-warm and browser, `fal-runner`, `lib/falmcp.sh` | validator | same tools | PASS, all four; two low notes (one fixed below as part of K3) |
| S1: security review of key handling (`lib/stackenv.py`, `lib/keys.sh` and their callers) | security-auditor, with four adversarial-verifier passes | static review; the verifiers reproduced the helper lookup in sh, bash, dash and zsh (SEC-4) and tested ignore patterns with `git check-ignore` (SEC-2) | FIX-FIRST: 1 Medium, 9 Low, 2 Info (the report's summary says ten Lows; nine are written up); one candidate (keys inherited by child processes) refuted |
| GATE2: installer wiring, doctor, new tests | validator | static: 35 of 35 profile values, 22 of 22 doctor ids, `py_compile` and an `ast` check that every name the tests import from `tests/helpers.py` exists (no import) | PASS; notes: two existing tests assert the old minimal pack list (owned by the test update), `--image-backend none` keeps the image packs (PITFALLS 55) |
| K3: fixes for S1 | general-purpose | additive changes only (function and option inventory 141 to 163, nothing removed) | table below |
| GATE3: K3 and the uninstaller changes | validator | static: `shellcheck -x`, `bash -n`, `py_compile`, `jq`; its own function and option inventory before and after K3 (171 to 204 entries, nothing removed); the bash and Python key-value rules compared on all 1,112,064 Unicode scalar values and 400,000 random byte strings (0 mismatches); every line K3 cited read | **PASS: K3 7 of 7, U1 8 of 8.** Notes, not failures: c101's own installer can still chmod an existing `stack.env` to 600 during a c101 reversal (mode only, never bytes); restoring the caller's traps after Ctrl-C at the prompt is unverified on bash 4.2/4.3 (V7) |
| T1, T2, T3: tests | tester | written; `ast`/`symtable` checks and `bash -n` on generated script bodies; one check imported `tests/helpers.py` (see "What ran on the Mac"); T2's modules passed `py_compile` and GATE2's `ast` import check; never run | see the test table above |
| DOC2: PITFALLS 43 to 59, UPSTREAM 13 to 26, section 5 of this file | general-purpose | written from the code and the node reports | done |
| RV1: code review of the whole change | reviewer, findings confirmed by adversarial-verifier | read-only review of the diff against the base commit, with callers in both repositories | **SHIP** (approve with comments): 0 S0, 0 S1, 2 S2 (both survived the verifier), 3 S3. R1 to R5 went to the fix step FX1, table below |
| DOC1: README, SCENARIOS, OUTCOME, this section | general-purpose | written from the code, the profiles and the node reports; read-only scratch scripts checked the 76 parity rows, the flags of `usage()` against the README and the README's profile table against `config/profiles/*.conf` | done |
| FX1: fixes for RV1 and T3's notes | general-purpose | code changes with 13 regression tests; `shellcheck -x`, `bash -n`, `py_compile` | **done**: R1 to R5 fixed (R1 through decision D12: `none` is sticky); T3 note 1 (the ci and `--yes` FAL_KEY hint) and note 3 (the doctor's child environment) fixed; note 2 (unit `.bak` leftovers) is pre-existing, from the managed-block backups of the base commit 96050d1, so not changed |
| FX2: the user's exact `install-linux.sh --keys` hint (decision D13) | general-purpose | hint and warning text, the tests that assert it, these docs; `shellcheck -x`, `bash -n`, `py_compile` | **done**: FX1 had made ci and `--yes` runs print only `install-linux.sh --keys-only --keys-from-env`, which dropped the re-run command the user asked for. Now every FAL_KEY warning and fix hint contains `install-linux.sh --keys` (only `--profile` and `--image-backend` follow it); on ci and `--yes` runs the same single warning and the closing next step also give the unattended form (export `FAL_KEY`, then `--keys-only --keys-from-env`); the interactive FAL_KEY warning and next step are unchanged. The doctor's fix is `install-linux.sh --keys` in every profile, plus the unattended form in ci. One test renamed, one added, none removed. FX3 (below) puts `--keys-only` right after that command (D14) |
| V1: final validation of the whole change | validator | static: `shellcheck -x` and `bash -n` on 20 shell files, `py_compile` on 21 Python files (bytecode in a scratch directory), `jq`, every node's acceptance re-checked, a secret scan of the diff, the write sets, c101 and the real HOME | **SHIP**: 0 must-criteria failed; runtime behaviour unverified by the user's constraint |
| QC: quality gate before delivery | quality-control | traceability of every requirement, the load-bearing claims re-checked statically | **FIX-FIRST**: 1 Blocker, 1 Major, 3 Minor, 1 Nit. QC-1 (Blocker): README and OUTCOME said only static checks ran on the Mac, but steps had imported repository modules and sourced repository shell functions in memory. QC-2 (Major): every key fix hint (`install-linux.sh --keys`) re-ran the whole installer, which re-applies the profile's defaults (`FAL_MCP=1`, `PRESET=everything`) over earlier choices such as `--preset research --no-fal-mcp`. QC-3: stale test count (379, really 393). QC-4: a stray `tests/__pycache__` from the `tests/helpers.py` import (git-ignored; removed). QC-5: the README's parity sentence did not name the 3 N/A features. QC-6 (Nit): the status word "fixed-by-plan". QC-1, 2, 3 and 5 went to FX3 |
| FX3: QC's punch list (decision D14) | general-purpose | code, tests and these docs; `shellcheck -x`, `bash -n` (bash 5.3), `py_compile` (bytecode in a scratch directory), `grep`; one stdlib scratch script checked the new tests' regular expressions on hand-written strings (it read no repository file) | **done**: every key fix hint is `install-linux.sh --keys --keys-only`, then `--profile` and `--image-backend` as before (D12): the user's literal `install-linux.sh --keys`, limited to the keys and image-backend step, so following it never re-applies profile defaults; ci and `--yes` runs keep the unattended `--keys-only --keys-from-env` form (D13), and the interactive closing step uses the same command. Built in `lib/keys.sh` (`keys_rerun_cmd`, `keys_rerun_hint`) and the doctor's `keys_fix_cmd`, which also builds the INFO hint that adds fal. The disclosure of what ran on the Mac now says the same in the README, OUTCOME section 5 and below; test counts 395; the README names the 3 N/A features and their alternatives. Two tests added (`test_no_key_hint_ever_lacks_keys_only`, `test_no_key_fix_reruns_the_full_install_over_recorded_choices`), the hint assertions now require `--keys-only`, none removed. The re-gates follow in the next rows |
| V2: re-run of the final validation after FX3 | validator | static, as V1 | **FIX-FIRST**, caused by the validator itself: its static-check loop emptied `install-linux.sh` (see "Incident during the re-gate after FX3" below). FX3's changes in `lib/keys.sh`, the doctor, the tests and the test counts passed; several checks were not re-run after the incident |
| QC re-gate 2 | quality-control | static: `shellcheck -x`, `bash -n`, `py_compile`, `jq`, `grep`, `git show`/`diff`/`status`, `patch` on a scratch copy | **FIX-FIRST**: QC2-1 (Blocker) `install-linux.sh` was 0 bytes; QC rebuilt its post-FX2 state in a scratch copy. QC2-2 (Minor) five PITFALLS fix commands not in the D14 form; QC2-3 (Minor) "T1 to T3 wrote 379" counted the 102 tests of the base commit; QC2-4 (Nit) the README said "scratch files" where K1 also read `/etc/hosts`. QC-1 to QC-5 resolved in the files that still existed |
| FX4: restore of `install-linux.sh`, QC2-2 to QC2-4 | general-purpose | the file restored from QC's copy plus FX3's `keys_summary` change; `shellcheck -x`, `bash -n` (bash 5.3), `grep`; one stdlib scratch script read `install-linux.sh` and `lib/keys.sh` and matched the installer tests' expected strings against the lines built from them | **done**: the restored file differs from QC's copy only in `keys_summary` and its comment; PITFALLS 46, 47, 48, 51 and 56 use the D14 form; the test-count wording and the README's K1 wording fixed. The re-gate after FX4 is not recorded here |

S1 findings and their resolution:

| Id | Finding | Resolution |
|---|---|---|
| SEC-4 (Medium) | `config/fal-mcp.json` ran `bin/mcp-headers` from the session's working directory | fixed path `exec "$HOME/.claude/stack/bin/mcp-headers" fal` in `config/fal-mcp.json` and, since the re-review fixes (section 7), in `agents/fal-runner.md` too (no `$CLAUDE_PROJECT_DIR`/`$PWD` lookup; UPSTREAM 22, V11) |
| SEC-2 | plain-text backups pile up next to a symlink target, possibly inside a git repository | nothing is deleted automatically; the merge reports the count; the installer prints the path, count and a gitignore hint; PITFALLS 54 |
| SEC-3 | a permissive existing `stack.env` was never tightened or reported | tightened to 600 even when nothing else changes; a file owned by another account is reported and left alone |
| SEC-5 | the read-back check modelled bash with Python's line splitting | CRLF rewritten to LF; other breaks refused (exit 3, `line-breaks`); bash readers modelled with LF splitting; PITFALLS 57 |
| SEC-6 | loose fal host test; placeholder replaced regardless of host | https, host exactly `queue.fal.run`, no userinfo or backslash, port 443 or none; the placeholder becomes the key only in a fal operation's KEY |
| SEC-7 | `FAL_KEY=... ./install-linux.sh` leaves the key in shell history | documentation: `--keys` first, history caveat next to every environment example; PITFALLS 46 |
| SEC-8 | echo may stay off after Ctrl-C at the hidden prompt on bash 4.2/4.3 | terminal mode saved and restored, the caller's traps reinstalled, the signal re-sent; V7 |
| SEC-9 | the y/N question after the prompts echoes input | rejected: the question is labelled and the input is never stored |
| SEC-10 | no ownership or permission check on the target's directory | `check` reports a foreign owner and group- or world-writable directories; warning only |
| SEC-11 | the bash and Python value checks differ | one control-character set on both sides |
| Info-1 | the `lib/keys.sh` contract did not require callers to un-export keys early | documented in the contract |
| Info-2 | backups written as root are not chowned | rejected: the installer refuses root, and the backup is mode 600 |

RV1 findings, all fixed by the fix step FX1 (checked statically; the final validation V1 is in the table above):

| Id | Severity | Finding | Fix asked for |
|---|---|---|---|
| R1 | S2 | after `--image-backend none`, the printed hint (`--keys-only --keys`) brought the profile's fal back: preset merged, state none to fal, doctor FAIL; `FAL_KEY` was reported missing on every `none` install | decision D12: `none` is sticky (no explicit `--image-backend` and state `none` keep `none`; only `--image-backend fal` re-enables it), hints carry the effective backend, `FAL_KEY` is reported as used by `fal-runner` when the backend is not fal; a regression test. README, SCENARIOS and OUTCOME document this behaviour |
| R2 | S2 | Alpine/OpenRC cron fallback: BusyBox `find` has no `-printf`, its stderr was hidden, so the backstop silently found "0 transcripts" | probe `-printf` first and exit 1 naming GNU find (`apk add findutils`), keep find's stderr, name findutils in the cron hint; a test with a stub `find` |
| R3 | S3 | `--keys-only` resolved c101 by the default search, not the installed checkout, so the fal preset was "not on disk" after an earlier `--source` install | fall back to the state's `c101_source`, then `readlink ~/.claude/stack`; add `--source` to the hint |
| R4 | S3 | the uninstaller disables the timer but does not stop a running backstop service before removing its stamps | stop `c101-learn-backstop.service` before the stamp purge |
| R5 | S3 | the doctor's hooks check took `--enforce-models` from the profile, not the recorded c101 flags, so `--profile full --no-enforce-models` got a false WARN | read the state's `c101_flags` when present |

### Incident during the re-gate after FX3

On 2026-11-14 at 16:04:22 the validator re-running the final gate (V2) ran a static-check loop (`shellcheck`, `bash -n`)
whose file list was unquoted. Its output redirects landed on paths inside the repository: the loop truncated
`install-linux.sh` to 0 bytes and created 19 zero-byte stray files in the repository root (`bin_c101-*`, `lib_*.sh`,
`tests_*.sh`, `uninstall-linux.sh.bn` and `.sc`), which it removed again. No other file was damaged: all of them stayed
non-empty and clean under `shellcheck -x` and `bash -n`. Nothing from either repository was executed.

No copy of the installer as FX3 left it existed. The quality gate rebuilt the last known earlier state in a scratch copy:
the base commit's `install-linux.sh` (`git show 96050d1:install-linux.sh`) plus the `install-linux.sh` part of a full diff
taken at 15:32:53, which is the state after FX2 and before FX3 (27,633 bytes). FX4 restored the file from that copy (mode
755 kept) and re-applied FX3's change to it, which by the run's record was confined to `keys_summary`: the comment above
it describes D14, the interactive closing step prints `keys_rerun_cmd` (`install-linux.sh --keys --keys-only`), ci and
`--yes` runs print `keys_rerun_hint` (that command and the unattended `--keys-only --keys-from-env` form), and the "Keys
still empty" step uses `keys_rerun_hint` too. Byte-identity with the lost FX3 version cannot be proven; the re-gate checks
the restored file's behaviour statically (`shellcheck -x`, `bash -n`, the D14 hint forms, the 11 new flags in `usage()`,
and the tests' expected strings by reading).

### What ran on the Mac

The user forbade testing on the Mac. This is everything that ran there during the parity change, compiled from every
step's report. (Before that instruction, for the first version, four read-only invocations of `install-linux.sh` ran:
`--help`, `--print-config` twice, once under `bash -x`, and one `--dry-run`, which exited 3 at the Linux-only gate; section
2, QC-1.)

* Never run by anyone: `install-linux.sh`, `uninstall-linux.sh`, any `bin/` script of either repository, c101's installer,
  `bin/stack`, podman, systemctl, npx, uvx, the Python test suite, `tests/matrix.sh`. `~/.claude/stack.env` was never read
  (only `stat`; its modification time stayed 2026-11-13 03:50). Nothing was written into the c101 checkout: its one
  modified agent file (frontmatter `maxTurns` and `autoCompactWindow` only) comes from c101's live learner and predates
  the run.
* Static tools: `shellcheck -x`, `bash -n`, `python3 -m py_compile` (bytecode in a scratch directory), `jq`, `grep`,
  `git diff`/`show`/`status` against the base commit, `stat`, and the orchestrator's read-only task-graph checker.
* Read-only scratch scripts outside the repository: GATE3's function and option inventories and DOC1's documentation
  checks (row and flag counts, profile values); FX3's check of the new tests' regular expressions on hand-written strings
  (it read no repository file); FX4's check that read `install-linux.sh` and `lib/keys.sh` as text, built the printed
  hint lines from them and matched the installer tests' expected strings (nothing sourced or imported).
* The incident during the re-gate after FX3 (section above): V2's static-check loop (`shellcheck`, `bash -n`), with an
  unquoted file list, truncated `install-linux.sh` to 0 bytes and created 19 zero-byte stray files in the repository root,
  which it removed again. The quality gate rebuilt the post-FX2 state with `git show` and `patch` in a scratch directory;
  FX4 restored the file from it and re-applied FX3's `keys_summary` change. No repository code was executed.
* Beyond static checks, all in scratch directories, with made-up key strings, no network and no file written in the real
  HOME:
  * K1: one scratch script imported `lib/stackenv.py` and called its pure functions (28 of 28 checks passed) and ran its
    read-only `names`, `check` and `ops` subcommands on a scratch path and `/etc/hosts`; the `merge` write path never ran.
  * K2: a stub harness sourced `lib/keys.sh` with its helper and state functions stubbed (no Python, no `stack.env`, whose
    modification time stayed unchanged), including one prompt run on an emulated terminal (`script`).
  * P1: sourced `lib/presets.sh` in bash for resolver cases, ran `lib/packs.py validate` and `resolve` against c101's
    `packs.json` (read-only) and drove the preset picker on a pseudo-terminal.
  * L1: ran awk and read-loop snippets extracted from `bin/c101-learn-backstop` on synthetic input (the script itself
    never ran).
  * H1 and the gate validators: rendered the fake test fixture texts and compared them with c101's example files (names
    and hosts only).
  * E1: imported `lib/desktop_config.py` in memory (merge and remove on in-memory dicts); a two-line bash test of ERR-trap
    semantics.
  * A1: imported c101's `stack/frontmatter.py` read-only to parse the new agent's frontmatter.
  * DR1: ran its five inline Python probe snippets against c101's real files and scratch fixtures.
  * K3 and GATE3: loaded the definitions through `ast` (no import) and compared the bash and Python value rules on all
    1,112,064 Unicode scalar values and 400,000 random byte strings (bytes in memory, no key file).
  * S1's adversarial verifiers: a reproduction of the helper lookup in sh, bash, dash and zsh, and a `git check-ignore`
    test in a dummy repository.
  * RV1's verifier: a bash-semantics demonstration with a stub `find`.
  * DOC2: ran the PITFALLS 57 line-break finder on a scratch fixture.
  * T1 to T3 (the test authors): `ast` and `symtable` checks and `bash -n` on generated script bodies; one check imported
    `tests/helpers.py`, which left `tests/__pycache__/helpers.cpython-313.pyc` in the repository (git-ignored; removed).
* These go beyond the allowed "stdlib scratch scripts that only read files" in spirit: repository modules were imported and
  repository shell functions were sourced, in memory, to check logic. No installer, service, container or network action
  resulted.

## 7. Re-review fixes (R1 to R6 and the fal-runner helper path; nothing executed)

The reviewer's report on the parity change (R1 to R6, `.stack/out/reviewer/parity-review.md`) was fixed in the working tree. Checked
statically only (the Mac must not run the repository): `shellcheck -x` and `bash -n` on every shell file, `py_compile` on
`lib/*.py` and `tests/*.py` (bytecode redirected outside the repository), `jq` on `config/*.json`, `grep` for re-run hints. The new
tests have not run: `tests/run-tests.sh` on Linux is still the open step.

| Id | Fix | Where | Tests |
|---|---|---|---|
| R1 (S1) | a run without `--profile` keeps the profile recorded in state (one info line; an explicit `--profile` wins; an unknown recorded name is ignored); every re-run hint is built by `rerun_cmd` (`lib/common.sh`), which appends `--profile NAME` unless it is standard; `keys_rerun_cmd` and the doctor's `keys_fix_cmd` use the same `rerun_profile_sfx` | `install-linux.sh` `profile_sticky`; `lib/common.sh`; `lib/prewarm.sh`, `deps.sh`, `desktop.sh`, `services.sh`; `bin/c101-linux-doctor` (34 hints) | `KeysOnlyTests`, `RerunHintTests` (static: no hint outside the builders), `PrewarmTests`, `RuntimeAndLearningTests` |
| R2 | `--mcp fal` and `--mcp fal-oauth` are refused (exit 2) with a pointer to the fal-runner agent and `claude --mcp-config ~/.config/c101/fal-mcp.json` | `lib/c101.sh` `refuse_unsupported_mcp`; README; SCENARIOS row 65 | `InstallerTests` |
| R3 | `settings_env_before` holds key names only; `jsontool.py env-snapshot` prints names, `strip-env` takes names (an older state's object still works, its keys count) and reads them from stdin (`-`), so no value reaches state or a command line; an older snapshot is cut down to names on the next install | `lib/jsontool.py`, `lib/c101.sh`, `uninstall-linux.sh` | `JsonToolTests`, `UninstallTests`, `RealInstallTests` |
| R4 | the image backend is recorded right after the key step, not only in the final state block | `install-linux.sh` | `RealInstallTests` |
| R5 | an uninstall without a state file removes the learning backstop timer, service and script at their default paths (marker-checked) | `lib/learning.sh` `learn_backstop_remove_defaults`, `uninstall-linux.sh` | `UninstallParityTests` |
| R6 | `--keys-only` words the FAL_KEY line by the recorded `fal_mcp` (a `--fal-mcp` / `--no-fal-mcp` flag still wins) | `install-linux.sh` `keys_fal_mcp_effective` | `KeysOnlyTests` |
| SEC-4 | `agents/fal-runner.md` calls `exec "$HOME/.claude/stack/bin/mcp-headers" fal`: no `$CLAUDE_PROJECT_DIR`/`$PWD` lookup, so the agent never runs a script from a project directory (the install is always user level) | `agents/fal-runner.md`; PITFALLS 52, UPSTREAM 22, OUTCOME question 11 | none (frontmatter text; V11 now concerns c101's own agents only) |

Decision on the standard profile: `--profile standard` is left out of hints because the installer's default, now also the default
of a run with a recorded standard profile, is standard; every other profile is named.
