# Scenarios and feature parity

What `install-linux.sh` does on each kind of system, and how c101's features map onto Linux. Statements about
Anthropic's software come from the research notes (code.claude.com/docs/en/desktop-linux, /desktop, /setup;
accessed 2026-11-14). Statements marked *unverified* were reasoned from the docs or the code, not observed on a machine.
Nothing in this repository has run on Linux yet ([OUTCOME](OUTCOME.md) section 5).

## 1. Feature parity: c101 on macOS vs c101Linux

One row per user-visible c101 feature (76), audited against c101's code before the parity change of 2026-11-14 and
mapped to its state after it. File references in the third column are c101's.

**fal image generation is c101's main use and is on in every profile.** Every profile sets `IMAGE_BACKEND=fal` and
installs the `image` and `graphic-design` packs. **`--image-backend none` opts out**, and the choice is sticky (section 4).
Without a fal key (unattended runs, `--yes`, no terminal, ci) the fal lines are still written to `stack.env` with an
**empty key**, so no image tool appears until the key is added; the installer warns once, naming `FAL_KEY`, and prints
the exact re-run command `install-linux.sh --keys --keys-only` (hidden prompt, from a terminal; `--keys-only` keeps the
re-run to the keys and image-backend step, so no profile default comes back over your earlier choices). On ci and
`--yes` runs the same warning also gives the unattended form: export `FAL_KEY` (e.g. a CI secret), then
`--keys-only --keys-from-env`.

The hosted fal MCP is installed by default but never loaded by default. The `fal-runner` agent loads it only while it
runs; a whole session is opt-in with `claude --mcp-config ~/.config/c101/fal-mcp.json`; `--fal-mcp` / `--no-fal-mcp`
decide whether either piece is installed. Presets and their packs are in section 4.

Status:
* **works**: no Linux-specific dependency; static evidence is enough.
* **unverified**: works by design but depends on the Linux runtime; the last column names the doctor check id or test that
  settles it.
* **fixed-by-plan**: missing, broken or gap-prone on Linux before this change. The fifth column names what implements the
  fix and the last column the check. None of these rows has run on Linux ([OUTCOME](OUTCOME.md) section 5 says what was
  checked on the Mac and how), so read them as "fixed, not yet run".
* **N/A**: cannot exist on Linux; the alternative is named.

Totals: works 44, unverified 6, fixed-by-plan 23, N/A 3. `V1` to `V12` refer to
[VERIFICATION.md](VERIFICATION.md) section 5; doctor ids are `bin/c101-linux-doctor` checks.

| # | Feature | c101 on macOS | Status | c101Linux after the parity change | Linux check |
|---|---|---|---|---|---|
| 1 | `stack doctor` | stack/doctor.py:571-579 | works | run at the end of `bin/c101-linux-doctor` | `c101-doctor` |
| 2 | `stack agents list/main-args/tiers/set` | cli.py:58-142; tiers.py:99-147 | works | pure Python; `agents set` edits maxTurns, effort and more by hand | - |
| 3 | `stack keys` | cli.py:164-180; policy.json expected_keys | fixed-by-plan | the key step (`lib/keys.sh`, `lib/stackenv.py`) provisions the keys it lists | `keys-research`; tests/test_keys.py |
| 4 | `stack learn ingest/update/apply/report/history/run` | cli.py:182-247; engine.py | fixed-by-plan | the learning backstop timer (`--learn-timer`, `bin/c101-learn-backstop`) runs `learn run` besides the hooks; the checkout must be writable | `learning-writable`, `learning-timer`, `learning-log`; V3 |
| 5 | `stack hook <name>` dispatcher | hooks.py:771-800; bin/stack, _pyenv.sh | unverified | needs c101's `.venv` Python or uv on the PATH of a GUI-launched Desktop (login-profile block, environment.d) | `hooks-runtime` |
| 6 | `stack milestone` | cli.py:270-290 | works | - | - |
| 7 | `stack progress [-f]` | cli.py:293-316; progress.py | works | `c101-stack progress -f` | - |
| 8 | `stack install` | install.py | works | run by the overlay with the profile's flags (`lib/c101.sh`) | - |
| 9 | `stack depth` statistics | cli.py:250-267 | works | per-session spawns by depth and type | `depth-stats` |
| 10 | `stack packs list/apply` (project scope) | packs.py:98-117 | works | - | - |
| 11 | `stack managed-settings` | managed.py (Linux: /etc/claude-code) | works | `--managed-settings` | `managed-settings` |
| 12 | `stack mcp list/enable/disable/sync` | cli.py:332-381 | works | `enable` edits the checkout (project scope, PITFALLS 52, UPSTREAM 20); the hosted fal MCP uses row 64 instead | - |
| 13 | SessionStart hook: depth line, missing-keys line, catch-up apply | hooks.py:507-542 | fixed-by-plan | the key step fills `EXA_API_KEY`, `JINA_API_KEY`, `SPIDER_API_KEY`, so the missing-keys line stops once they are set (it stays after a run without keys, PITFALLS 51) | `keys-research` |
| 14 | PreToolUse pre-agent: depth, spawn_rules, budgets, tier strip | hooks.py:185-315 | works | - | `hooks-events` |
| 15 | PreToolUse pre-tool: milestones, taint, outward-action ask | hooks.py:713-760 | works | - | `hooks-events` |
| 16 | SubagentStop: done milestone, debounced ingest/update | hooks.py:463-493 | works | - | `hooks-events` |
| 17 | SessionEnd: prune and detached `learn run` | hooks.py:318-330, 496-503 | fixed-by-plan | the backstop timer learns from sessions that ended without SessionEnd (Desktop quit, logout, crash); whether a Desktop quit fires SessionEnd is itself unverified | `learning-timer`; V3 (`LearningBackstopRuntimeTests`, `DesktopQuitManualCheck`) |
| 18 | UserPromptSubmit: token budget, milestone digest | hooks.py:564-582 | works | - | `hooks-events` |
| 19 | PreModelSwitch (`--enforce-models`) | hooks.py:590-606 | works | profile full | `hooks-events`, `enforce-models` |
| 20 | PostToolUse post-agent digest | hooks.py:674 | works | - | `hooks-events` |
| 21 | PostToolUse post-web injection scoring | hooks.py:639; injection.py | works | - | `hooks-events` |
| 22 | Settings env: depth 8, MAX_CONCURRENT_SUBAGENTS 20, 16 workflow agents, UV_PYTHON | .claude/settings.json; install.py:291-316 | works | written by c101 | `settings-env` |
| 23 | Hook runtime (`.venv` Python, uv fallback) under the Desktop Code tab | _pyenv.sh:15-26 | unverified | depends on the GUI PATH (login-profile block, environment.d) | `hooks-events`, `hooks-runtime`, `login-path-effective` |
| 24 | Delegator as main agent (`--main-agent`) | install.py:350-357 | works | profiles standard, full, headless | `main-agent` |
| 25 | `--enforce-models` (availableModels) | install.py:322-347 | works | profile full | `enforce-models` |
| 26 | Managed settings file | managed.py:29-57 | works | `--managed-settings` | `managed-settings` |
| 27 | Tier enforcement, `agents tiers --apply` | hooks.py:169-182; tiers.py | works | - | - |
| 28 | uv Python default (`--no-python-default` off) | install.py:119-165 | works | PITFALLS 20 | - |
| 29 | Workflow swarm-run | .claude/workflows/swarm-run.js | works | linked by c101 `--user` | `workflows-linked` |
| 30 | Workflow fanout | fanout.js | works | as 29 | `workflows-linked` |
| 31 | Workflow mediate | mediate.js | works | as 29 | `workflows-linked` |
| 32 | Workflow expert-panel | expert-panel.js | works | as 29 | `workflows-linked` |
| 33 | Workflow plan-tournament | plan-tournament.js | works | as 29 | `workflows-linked` |
| 34 | Workflow research-sweep | research-sweep.js | works | as 29; uses the research servers (rows 54-58) | `workflows-linked`, `keys-research` |
| 35 | Workflow review-verify | review-verify.js | works | as 29 | `workflows-linked` |
| 36 | Workflow visual-iterate | visual-iterate.js | works | as 29; uses the image tools (row 59) | `workflows-linked`, `image-router` |
| 37 | Workflow manuscript-review | manuscript-review.js | works | as 29 | `workflows-linked` |
| 38 | Depth guard and spawn_rules | hooks.py:75-80, 147-166 | works | - | - |
| 39 | Spawn budget (256 per session) | hooks.py:235-242; policy.json | works | c101's docs/DEPTH.md says 128 (UPSTREAM 16) | - |
| 40 | Session statistics (spawns, by_depth, by_type) | hooks.py:120-144 | works | `c101-stack depth` | `depth-stats` |
| 41 | Spawn/stop ledger | hooks.py:331-361, 413-431 | works | - | - |
| 42 | Token budget (usage.json) | budget.py:79-195 | works | - | - |
| 43 | Milestones and progress relay | progress.py:74-410 | works | - | - |
| 44 | Prompt-injection taint guard | injection.py; hooks.py:746 | works | - | - |
| 45 | Outward-action guard (push, kube) | hooks.py:713-760 | works | - | - |
| 46 | Learned maxTurns, effort, autoCompactWindow written to frontmatter | engine.py:342-392; frontmatter.py | unverified | written through the `~/.claude/agents` links into the checkout, which must be writable (PITFALLS 49, 59) | `learning-writable` |
| 47 | Apply at SessionEnd, catch-up at SessionStart | learning.json apply_on; hooks.py:514-539 | fixed-by-plan | the backstop's `learn run` also applies after a missed SessionEnd | `learning-timer`; V3 |
| 48 | Learning statistics `learn history/report` | engine.py:446 | fixed-by-plan | the backstop writes the history row a missed SessionEnd would lose | `learning-log`; V3, V9 (tests/test_learning_timer.py) |
| 49 | stack.env from the template, mode 600 | install.py:86-95 | works | created by c101; the key step also tightens a readable file to 600 | `stack-env`, `stack-env-mode` |
| 50 | Research and other key provisioning | user edits stack.env by hand | fixed-by-plan | `--keys`, `--keys-from-env`, `--keys-only`, `--keys-overwrite`, profile key KEYS (`lib/keys.sh`, `lib/stackenv.py`) | `keys-research`; tests/test_keys.py; V5, V6, V7 |
| 51 | Image backend config `IMAGE_<OP>_URL/KEY/MODEL(/API/PARAMS)` | image-ops.json; image_router/config.py | fixed-by-plan | `--image-backend fal` (every profile's default) writes them; another provider's operation is kept whole | `keys-image` |
| 52 | fal preset | config/stack.env.fal.example (9 operations) | fixed-by-plan | merged with one `FAL_KEY` for all nine fal operations; empty keys when none is given | `keys-image`, `image-router` |
| 53 | MCP playwright | mcp.servers.json:4-16 (Chrome channel) | fixed-by-plan | `--playwright-browser` (standard, full, headless): Playwright's Chromium at user level plus `PLAYWRIGHT_MCP_BROWSER=chromium` (`lib/prewarm.sh`); minimal and ci need that flag and `--path-integration`; Alpine uses the distro browser (PITFALLS 43) | `playwright-browser`; V1, V2, V12 |
| 54 | MCP exa (HTTP, key optional) | mcp.servers.json:17-21; mcp-headers | fixed-by-plan | `EXA_API_KEY` from the key step; without it anonymous with rate limits | `keys-research` |
| 55 | MCP jina (HTTP) | :22-26; mcp-headers | fixed-by-plan | `JINA_API_KEY` from the key step; without it anonymous with rate limits | `keys-research` |
| 56 | MCP spider (HTTP, key required) | :27-31; mcp-headers | fixed-by-plan | `SPIDER_API_KEY` from the key step | `keys-research` (FAIL without a key outside ci) |
| 57 | MCP context7 (HTTP, key optional) | :32-36 | fixed-by-plan | `CONTEXT7_API_KEY` (optional) from the key step | `keys-research` |
| 58 | MCP fetch (`uvx --python 3.13 mcp-server-fetch`) | :53-60 | fixed-by-plan | pre-warmed by `--prewarm-mcp` (every profile except ci) so the first start does not time out; uvx on the GUI PATH | `mcp-fetch-cache`; V8 |
| 59 | MCP image router tools (generate, edit, ...) | image-ops.json:101-317 | fixed-by-plan | listed once the fal preset and `FAL_KEY` set URL, KEY and MODEL; with an empty key none appear, by design | `image-router`, `keys-image`; V10 |
| 60 | Image local tools (svg_render, local TRACE/UPSCALE, PIL) | pyproject.toml; uv.lock (manylinux, musllinux wheels) | works | - | `image-deps` |
| 61 | MCP desktop-sandbox | bin/desktop-sandbox | unverified | rootless podman with the `c101-podman` wrapper (`--sandbox`, profile full) | `podman-info`, `cgroup-delegation`, `sandbox-image`; tests/matrix.sh |
| 62 | MCP neural-memory (`uvx --from neural-memory==4.62.0`) | bin/neural-memory-mcp | fixed-by-plan | pre-warmed by `--prewarm-mcp` | `neural-memory-cache`; V8 |
| 63 | Built-in computer-use | computer-operator.md | N/A | macOS only. Alternative: `desktop-sandbox`, a disposable, network-less Linux desktop under rootless podman; `computer-operator` falls back to it | `sandbox-image` |
| 64 | Hosted fal MCP (FAL_KEY via mcp-headers) | mcp.optional.json:4-9 | fixed-by-plan | the `fal-runner` agent (server inline, loaded only while it runs) and `~/.config/c101/fal-mcp.json` for `claude --mcp-config`; `--no-fal-mcp` opts out; never a user-scope registration (`lib/falmcp.sh`) | `fal-runner`, `fal-mcp-config`, `fal-mcp-reach`; V4, V11 |
| 65 | fal-oauth, recraft, flux (OAuth HTTP) | mcp.optional.json:10-34 | partly | recraft and flux: `--mcp` passthrough; the OAuth sign-in needs a browser. `--mcp fal` and `--mcp fal-oauth` are refused (exit 2): c101 registers them at user scope, so fal's ~60 tools would load in every session; use the `fal-runner` agent or `claude --mcp-config ~/.config/c101/fal-mcp.json` (row 64) | - |
| 66 | replicate, runware, brave-search (npx via mcp-run) | mcp.optional.json | fixed-by-plan | the key step imports or asks `REPLICATE_API_TOKEN`, `RUNWARE_API_KEY`, `BRAVE_API_KEY` (directly when the server is in `--mcp`); npx on the GUI PATH | `node`, `login-path-effective` |
| 67 | firecrawl, tavily (HTTP, keys) | mcp.optional.json | fixed-by-plan | the key step imports or asks `FIRECRAWL_API_KEY`, `TAVILY_API_KEY` | manual: `/mcp` shows the server connected |
| 68 | markitdown (uvx) | mcp.optional.json | fixed-by-plan | pre-warmed when it is in `--mcp` | V8 |
| 69 | peekaboo | mcp.optional.json:45-54 | N/A | refused by `--mcp`. Alternative: `desktop-sandbox` | - |
| 70 | Adobe servers and setup | adobe.py (darwin) | N/A | `--adobe` is refused: the apps exist for macOS and Windows only. Alternative for vector and raster work: the `image` server and the sandbox (Inkscape, GIMP or Krita via `bin/desktop-sandbox --profile recommended --packages "krita inkscape"`) | - |
| 71 | `bin/claude-stack` | bin/claude-stack:11-22 | unverified | linked into `~/.local/bin`; needs the `claude` CLI; symlink resolution (PITFALLS 22, UPSTREAM 7) | `command-links` |
| 72 | SDK runner `bin/stack-sdk` | stack_sdk/ | unverified | linked into `~/.local/bin` | `stack-sdk` (profile full) |
| 73 | mcp-run / mcp-headers key injection | mcp-run, mcp-headers | works | read `stack.env` at each start; the only place servers get keys | `stack-env` |
| 74 | Packs (`--packs`) | install.py; packs.json (21 packs) | works | passthrough; a command-line selection replaces the profile's (section 4) | - |
| 75 | Named agent and skill presets | none upstream (packs only) | fixed-by-plan | `--preset`, `--pick-preset`, profile key PRESET (`config/presets.conf`, `lib/presets.sh`, `lib/packs.py`) | tests/test_presets.py |
| 76 | Overlay Desktop chat MCP (`config/desktop-mcp.json`) | overlay only | fixed-by-plan | the `c101-playwright` entry passes `--browser chromium`; an older entry of ours is replaced on merge | `desktop-config`; tests/test_desktop_config.py |

## 2. Scenario matrix

Desktop route: **official** = Anthropic's signed APT repo (`--install-desktop`); **community** = AUR / aaddrick /
claude-desktop-extra, printed as guidance, never fetched; **none** = CLI only. In every row fal image generation and the
research servers are configured the same way; only where the keys come from differs (prompt on a terminal, the
environment in headless and ci).

| System | Desktop | CLI (`--install-cli`) | What the installer does |
|---|---|---|---|
| Ubuntu 24.04 / 22.04, Debian 12 (amd64, arm64) | official | signed apt repo | everything: prerequisites, uv, Node from distro (22.04 has Node 12: prints the version-manager fix), c101, keys and fal, PATH block + environment.d, pre-warm, Playwright's Chromium (arm64 builds exist for these releases), learning timer, podman layer in `full`. Ubuntu 23.10+/24.04 restrict unprivileged user namespaces (AppArmor): noted by the checks |
| Linux Mint, Pop!_OS, other Debian derivatives | official, "may work" (Anthropic lists Ubuntu/Debian only) | signed apt repo | same, with a warning |
| Debian 11, Ubuntu 20.04, anything with glibc < 2.34 | none (needs libc6 >= 2.34) | official installer script (Ubuntu 20.04+/Debian 10+ supported by the CLI) | Desktop step prints why; rest works. On arm64 Playwright has no Chromium build for these releases (PITFALLS 43) |
| Fedora, RHEL, Rocky, Alma, openSUSE | community only (Anthropic: "coming in the future") | official installer script | the Desktop checks (profiles standard, full, minimal) print the community options without any flag; podman via dnf/zypper; SELinux notes; PATH block; Playwright's generic Chromium build (*unverified*) |
| Arch, Manjaro, EndeavourOS | community: AUR `claude-desktop` (repackages the official build) | official installer script | the Desktop checks print the AUR hint; podman via pacman; generic Chromium build (*unverified*) |
| NixOS | community flake | official installer script (needs nix-ld, see below) | **preflight first**: stock NixOS cannot run astral's uv or the Python builds uv downloads, so the installer does not fetch uv there; it requires nixpkgs' `uv` and `programs.nix-ld.enable = true` and otherwise prints the module snippet (nix-ld, `uv`, `virtualisation.podman` with `dockerCompat`) and stops (`C101L_SKIP_NIXOS_CHECK=1` overrides). Packages are never installed imperatively; the PATH block is written (a home-manager symlink to `/nix/store` is refused with the block printed). A `stack.env` linked into `/nix/store` is refused by the key step (PITFALLS 45). `--keys-only` skips the preflight |
| Fedora Silverblue/Kinoite/Bazzite (ostree) | none directly | official installer script (user space) | no package layering: prints `rpm-ostree install` / toolbox / distrobox guidance. Podman is already part of these images. A distrobox with Ubuntu 24.04 can host the official Desktop (*unverified here*) |
| SteamOS (Steam Deck) | none directly | official installer script | read-only root: prints distrobox or `steamos-readonly disable` guidance |
| Alpine (musl) | none (needs glibc) | official installer script (needs bash + curl, installed first) | rootless podman via apk (`shadow-uidmap`); no systemd: units skipped, manual `podman system service` printed, and the learning timer becomes a printed cron line (the backstop needs GNU find: `apk add findutils`). Playwright's Chromium is glibc-only: the browser step is skipped with the `apk add chromium` route (PITFALLS 43). c101's Python wheels on musllinux are *unverified* |
| Void, Gentoo, other | none | official installer script | prints guidance; no package manager actions |
| WSL2 | not recommended: use the Windows Desktop app | official installer script | profile `headless` (keys from the environment); systemd units, the learning timer included, only when WSL has `systemd=true`; podman in WSL needs the usual rootless setup |
| Docker/Podman container, CI | none | installer script | profile `ci` (`--yes --no-sudo`, keys from the environment only, empty fal keys and a single warning without `FAL_KEY`; the doctor WARNs instead of FAILing), `--allow-root` for a root container |
| SSH-only server | none | signed apt repo or installer script | profile `headless` (keys from the environment, or `--keys` for the prompt); PATH block for login shells; `--enable-linger` keeps user units alive after logout |
| Ubuntu 22.04 (systemd 249) + rootless podman | official | signed apt repo | the user manager delegates `memory pids` but not `cpu`, so c101's sandbox (`--cpus`) cannot start: the installer offers the `user@.service.d/delegate.conf` drop-in (sudo, or printed with `--no-sudo`), the doctor FAILs until it is active |
| Docker already in daily use (docker-ce) | any | any | `DOCKER_HOST` is not pointed at Podman (that would hide your containers); `--docker-host-podman` forces it |
| NFS-mounted home | works, with caveats | CLI warns about Bus errors on shared NFS homes | rootless podman storage on NFS falls back to `vfs` (doctor warns); see PITFALLS |
| `stack.env` linked from a dotfiles repository | any | any | written through the link; the `.bak-<UTC stamp>` backups land beside the real file, inside that repository: add `stack.env.bak-*` to its `.gitignore` (PITFALLS 54) |

## 3. What each profile touches

| Path or step | minimal | standard | full | headless | ci |
|---|---|---|---|---|---|
| `~/.claude/*` (via c101) | core + image + graphic-design | all packs | all packs + model enforcement | all packs | core + image + graphic-design |
| `~/.claude/stack.env`: keys (KEYS) | environment, then prompt on a terminal | environment, then prompt on a terminal | environment, then prompt on a terminal | environment only | environment only |
| `~/.claude/stack.env`: fal image lines (IMAGE_BACKEND) | fal | fal | fal | fal | fal |
| `~/.claude/agents/fal-runner.md` link and `~/.config/c101/fal-mcp.json` (FAL_MCP) | yes | yes | yes | yes | yes |
| MCP pre-warm into the uv and npm caches (PREWARM_MCP) | yes | yes | yes | yes | no |
| Playwright's Chromium in `~/.cache/ms-playwright`, `PLAYWRIGHT_MCP_BROWSER` export (PLAYWRIGHT_BROWSER) | no | yes | yes | yes | no |
| learning timer `c101-learn-backstop.{service,timer}`, `~/.local/bin/c101-learn-backstop` (LEARN_TIMER) | no | yes | yes | yes | no |
| login-profile PATH block | no | yes | yes | yes | no |
| `~/.config/environment.d/60-c101-linux.conf` | no | systemd sessions | systemd sessions | no | no |
| `~/.local/bin/{claude-stack,stack-sdk,c101-stack}` | no | yes | yes | yes | no |
| `~/.local/bin/c101-podman`, `DESKTOP_SANDBOX_RUNTIME` export | no | yes | yes | yes | no |
| podman packages, sandbox image | no | no | yes | no | no |
| Podman units `~/.config/systemd/user/c101-podman-*`, `c101-sandbox-image.*`, `DOCKER_HOST` export (auto) | no | no | yes | no | no |
| Desktop checks (keyring, KVM, Wayland) | yes | yes | yes | no | no |
| system packages, apt sources | only what flags ask for | same | same | same | printed, not run (`--no-sudo`) |

Every value above can be changed with a flag; `--print-config` shows the result before anything runs. The uninstaller
removes every path in this table that the state file recorded, except `stack.env` and its backups (your keys) and the
caches and Chromium download, which it lists.

## 4. Presets and the image backend

| Preset | Packs (`core` is always installed) | Default of |
|---|---|---|
| `research` | core, coding-core | - |
| `coding` | coding-core, python, typescript, rust, cpp, go, java, kotlin, haskell, julia, devops, containers | - |
| `image` | image, graphic-design | minimal, ci |
| `science` | mathematics, physics, quantum, computer-science, computer-graphics, julia | - |
| `writing` | writing | - |
| `devops` | devops, containers, python | - |
| `everything` | every pack | standard, full, headless |

Selection rules:
* `--preset` (repeatable), the presets picked with `--pick-preset` and `--packs` are unioned into one command-line selection.
* That selection **replaces** the profile's `PRESET` and `C101_PACKS`. Without one, the profile's values apply.
* `everything` or the pack name `all` selects every pack.
* The fal backend always adds the `image` preset (image + graphic-design).
* c101 adds each pack's own requirements.
* Unknown preset or pack names stop the run with exit 2, before c101's installer runs.

Image backend (`--image-backend`, profile key `IMAGE_BACKEND`):
* `fal`, the default of every profile, merges c101's fal example into `stack.env`. One `FAL_KEY` fills the KEY of every
  operation whose URL is `https://queue.fal.run`, and `FAL_KEY` itself.
* `none` opts out and changes no image setting. It is **sticky**: the state file records it, and a later run without
  `--image-backend` stays on `none`. Only `--image-backend fal` re-enables fal. With `--fal-mcp` (the default) `FAL_KEY` is
  still asked for, because `fal-runner` uses it, but it does not turn the image backend back on.
* `none` does not drop the packs a profile's `PRESET=image` installs (minimal, ci). Choose packs on the command line for
  that, e.g. `--image-backend none --preset research` (PITFALLS 55).
* `keep` leaves the image settings alone and keeps the recorded backend.
* Empty-key behaviour: without a fal key the URL and MODEL lines are written with empty KEY values, so no image tool is
  listed until the key is added. One warning names `FAL_KEY` and prints the exact re-run command
  `install-linux.sh --keys --keys-only` (hidden prompt, from a terminal). `--keys-only` limits the re-run to the keys and
  image-backend step, so following the hint never re-applies the profile's defaults (`FAL_MCP`, `PRESET`) over the
  choices of an earlier install such as `--preset research --no-fal-mcp`. On ci and `--yes` runs the same single warning,
  and the closing next step, also give the unattended form: export `FAL_KEY` (e.g. a CI secret, never typed on the
  command line), then run `install-linux.sh --keys-only --keys-from-env`. Each printed command carries `--profile` and a
  non-fal `--image-backend` when they apply. The doctor's `keys-image` and `image-router` checks FAIL, or WARN in ci;
  their fix, like the doctor's fix for every other missing key, is `install-linux.sh --keys --keys-only` in every
  profile, plus the same unattended form in ci.

Hosted fal MCP (`--fal-mcp` / `--no-fal-mcp`, profile key `FAL_MCP`, 1 in every profile):
* Installed means the `fal-runner` agent link plus `~/.config/c101/fal-mcp.json`.
* It is never loaded by default. The agent connects to `https://mcp.fal.ai/mcp` only while it runs, and the whole-session
  config is opt-in: `claude --mcp-config ~/.config/c101/fal-mcp.json`.
* `--no-fal-mcp` installs neither piece.
* Whether fal accepts the key header for tool calls, or only OAuth, is unverified (PITFALLS 53).

## 5. Claude Desktop on Linux

Features of Anthropic's Desktop app, not of c101, that change on Linux:

| Feature | macOS | Linux | Notes |
|---|---|---|---|
| Claude Desktop install | cask/dmg | official signed `.deb` repo on Debian 12+/Ubuntu 22.04+ | other distros: guidance only |
| GUI-launched app finds uv, node, npx, podman | macOS login environment | login-profile block + environment.d | c101's launchers hard-code only macOS/nix paths; restart Desktop after the install |
| Agents in the Code tab | yes | *unverified* | Anthropic's docs do not say whether the Code tab loads `~/.claude/agents` (PITFALLS 15); skills, hooks and settings are shared |
| Cowork (VM) | yes | needs `/dev/kvm` + kvm group | doctor reports it; not needed for Chat or Code |
| Computer Use / Dictation in Desktop | yes | **N/A** in the Linux build | Anthropic: not available on Linux yet |
