# c101Linux

> **Status: version 0.1.0 plus the parity change of 2026-11-14. It has never been run on Linux.** It was built on a Mac,
> where the user forbade testing. During the parity change nobody ran `install-linux.sh`, `uninstall-linux.sh`, the doctor or
> any other `bin/` script, c101's installer, `bin/stack`, podman, systemctl, npx, uvx, the test suite or the distro matrix,
> and `~/.claude/stack.env` was never read. The code was checked with `shellcheck`, `bash -n`, `py_compile`, `jq` and `grep`
> and reviewed by reading. Beyond those static checks, and against the spirit of that instruction, some steps ran
> repository code in memory, in scratch directories with made-up keys and no network: they imported `lib/stackenv.py`
> (28 checks of its pure functions; its read-only `names`, `check` and `ops` on a scratch path and on `/etc/hosts`),
> `lib/desktop_config.py`, c101's `stack/frontmatter.py` and `tests/helpers.py`; sourced `lib/keys.sh` in a stub harness
> and `lib/presets.sh`; ran `lib/packs.py` on c101's `packs.json`, snippets of `bin/c101-learn-backstop` and a few small
> probes. No installer, service, container or network action resulted. One incident: a gate's unquoted shell loop truncated
> `install-linux.sh` to 0 bytes and left 19 zero-byte stray files (removed); the file was restored from a rebuilt post-FX2
> copy plus FX3's `keys_summary` edits, byte identity with the lost version cannot be proven, and a static re-gate
> followed (VERIFICATION section 6). Earlier, before that instruction, four read-only
> invocations of `install-linux.sh` (`--help`, `--print-config` twice, one `--dry-run` that stopped at the Linux check)
> ran there for the first version. The tests and the distro matrix are written but unrun, so expect defects on first
> contact. Everything below describes intended behaviour. Details: [docs/OUTCOME.md](docs/OUTCOME.md) section 5 and
> [docs/VERIFICATION.md](docs/VERIFICATION.md) section 6, "What ran on the Mac", which lists every step. No license is set
> yet (the owner's choice before publishing).

A Linux installer and configuration overlay for the [c101](https://github.com/rustcircle/c101) Claude Code agent stack
(64 agents, 239 skills, 9 workflows, hooks, MCP servers, self-tuning), targeting **Claude Desktop on Linux (its Code
tab)** and the **Claude Code CLI**, with the same features as c101 on macOS and rootless Podman in place of Docker,
except three macOS-only features that are N/A on Linux: built-in computer use (c101's `computer-use` server) and
`peekaboo`, whose Linux alternative is the `desktop-sandbox`, a disposable, network-less Linux desktop under rootless
podman that `computer-operator` falls back to (it cannot see your own apps); and the Adobe apps (`--adobe` is refused),
whose alternative for vector and raster work is c101's `image` server plus Inkscape, GIMP or Krita in that sandbox. The
parity table is [SCENARIOS](docs/SCENARIOS.md) section 1.

**The main use of c101 is image generation through fal**: c101's `image` server and its `image-generator`, `image-editor`
and `vector-artist` agents generate, edit, cut out, upscale and vectorise images with fal.ai models. Every profile of this
installer sets that up. Next come research (exa, jina, spider, fetch and context7), agent and skill presets, swarms and
workflows, the delegator, session statistics, and maxTurns and effort that adjust themselves as you work.

## Quick start: image generation with fal

Needs Linux, **bash >= 4.2**, a normal user (the installer refuses root unless `--allow-root`) and a fal API key
(fal.ai dashboard). `sudo` is used only for package installs and system files; `--no-sudo` prints those commands instead.

```bash
git clone <this repo> c101Linux && cd c101Linux
# c101 itself: put a checkout next to this repo (../c101), pass --source PATH, or let the installer clone it.

./install-linux.sh --dry-run        # optional: the resolved plan; changes nothing
./install-linux.sh                  # profile "standard": asks for FAL_KEY (hidden input), then the research keys
bin/c101-linux-doctor               # "Image generation (fal)" comes first, then "Research servers"
```

1. **fal (image generation).** On a terminal the installer asks for `FAL_KEY` first, with hidden input (`read -s`); Enter
   keeps the current value. `--keys` asks even with `--yes`. Or pass it in the environment:

   ```bash
   FAL_KEY=... ./install-linux.sh
   ```

   That line lands in your **shell history** (and in the installer's `/proc/<pid>/environ` while it runs, readable by your
   own user). Prefer the hidden prompt (add `--keys` to get it even with `--yes`; later,
   `./install-linux.sh --keys --keys-only` asks for the keys alone). The environment form without a history entry is
   in [PITFALLS 46](docs/PITFALLS.md) (a leading space plus `read -rs FAL_KEY`, then `--keys-from-env`). One fal key fills
   `FAL_KEY` and the KEY of each of c101's nine fal image operations (GEN, REF, EDIT, TRANSPARENT, SEGMENT, BG_REMOVAL,
   UPSCALE, VECTOR, TRACE).
2. **Research (exa, jina, spider, fetch, context7).** The prompt asks right after `FAL_KEY`: `EXA_API_KEY`,
   `JINA_API_KEY`, `SPIDER_API_KEY`, then `CONTEXT7_API_KEY` (optional). The environment form, with the same history caveat:

   ```bash
   FAL_KEY=... EXA_API_KEY=... JINA_API_KEY=... SPIDER_API_KEY=... CONTEXT7_API_KEY=... ./install-linux.sh
   ```

   c101 registers all five research servers in every profile. exa and jina also work without a key, with rate limits;
   spider needs one; context7's key is optional; fetch needs none and is pre-warmed (every profile except ci), so its first
   start does not time out.
   Optional servers' keys (`FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `REPLICATE_API_TOKEN`, `RUNWARE_API_KEY`)
   come last, behind a y/N question unless their server is in `--mcp`.
3. **Check.** `bin/c101-linux-doctor` reports every image operation and research key by name (configured, key empty, not
   configured), never a value. A fal backend with an empty key is a FAIL (a WARN in the ci profile), with the command that
   fixes it. Add or change keys later with `./install-linux.sh --keys --keys-only` (nothing else runs).

### Where the keys live

* **At run time the servers read keys ONLY from `~/.claude/stack.env`** (or the file `$CLAUDE_STACK_ENV` names, when Claude
  starts with that variable set). c101's `bin/mcp-run` passes them to the stdio servers (image, fetch); `bin/mcp-headers`
  turns them into HTTP headers for exa, jina, spider, context7 and fal.
* The servers **re-read the file on each launch or connection**. After editing it, restart Claude or reconnect with `/mcp`;
  **no reinstall** is needed ([PITFALLS 56](docs/PITFALLS.md) lists when each server picks up an edit).
* **The installer only seeds that file.** It writes through `lib/stackenv.py`, which keeps mode 600 and writes atomically
  through a symlink. Before any change it saves a backup, `stack.env.bak-<UTC stamp>` (mode 600, never deleted
  automatically). It keeps existing values unless `--keys-overwrite`, and prints only key names. Values reach it on stdin,
  never on a command line.
* Never put keys into `environment.d`, the login profile, systemd units, `claude_desktop_config.json` or `settings.json`.
  The installer writes none of those, and the state file holds no key values and none of the stack's keys: for
  `settings.json` it records only the *names* of the `env` variables that were there before c101 ran (so the uninstaller
  removes only what c101 added), never their values; an older state file's snapshot is cut down to its names on the next run.

### fal is on in every profile

* Every profile sets `IMAGE_BACKEND=fal` and installs the `image` and `graphic-design` packs.
* **`--image-backend none` opts out**: the run changes no image setting in `stack.env`. The choice is **sticky**: the state
  file records it, and later runs without `--image-backend` keep `none`. Only an explicit `--image-backend fal` turns fal
  back on.
* The installer's and the doctor's hints follow the effective backend. With the default `--fal-mcp` the installer still
  asks for `FAL_KEY` (the `fal-runner` agent below uses it), but that never re-enables the image backend.
* `none` does not remove the image packs that the minimal and ci profiles install through their preset. Choose packs on the
  command line to drop them ([PITFALLS 55](docs/PITFALLS.md)).
* `--image-backend keep` leaves `stack.env` as it is and keeps the backend recorded earlier.
* **Without a fal key** (`--yes`, no terminal, `--keys-from-env` without `FAL_KEY`, headless or ci without the variable),
  the installer still writes the fal URL and MODEL lines, with **empty KEY values**, so no image tool appears until a key is
  added. It warns once, naming `FAL_KEY`, and prints the exact re-run command `install-linux.sh --keys --keys-only` (the
  hidden prompt, run from a terminal). `--keys-only` limits that run to the keys and image-backend step, so following a hint
  never re-applies profile defaults over your earlier choices (a `--preset`, `--no-fal-mcp` and the like stay as they
  are). On ci and `--yes` runs the same warning, and the closing next step, also give the unattended form: export
  `FAL_KEY` (e.g. a CI secret, never typed on the command line), then `install-linux.sh --keys-only --keys-from-env`.
  `--profile` is added when it is not standard, and `--image-backend` when the effective backend is not fal, so a hint
  never turns fal back on. The doctor prints the same fix for every missing key: `install-linux.sh --keys --keys-only` in
  every profile, plus the unattended form in ci.
* An image operation you configured with another provider is kept whole and reported by its name; `--keys-overwrite`
  replaces it with fal, after a backup.

### The hosted fal MCP: the `fal-runner` agent

fal's hosted MCP server (`https://mcp.fal.ai/mcp`, about 60 tools) is **installed by default but not loaded by default**:

* **The `fal-runner` agent** is linked to `~/.claude/agents/fal-runner.md` and carries the server inline, so the server loads
  only while that agent runs. Its tools never appear in every session. Ask for it by name, e.g. "use fal-runner to turn this
  image into a 5-second video". It covers fal's model catalog, video, audio, 3D, the image endpoints c101's `image` server
  does not wrap, and job management. It starts a paid run without asking only up to USD 0.50, or within the limit your
  request names, and hands plain image work back to the image agents.
* **For a whole session** (CLI): `claude --mcp-config ~/.config/c101/fal-mcp.json` (under `$XDG_CONFIG_HOME` when set). Claude
  Desktop has no such option; use the agent there.
* **`--no-fal-mcp`** installs neither piece. The server is never registered at user scope.
* The key is `FAL_KEY` from `stack.env`. Whether fal accepts that key header for tool calls, or only OAuth, is unverified
  ([PITFALLS 53](docs/PITFALLS.md), [VERIFICATION](docs/VERIFICATION.md) V4).

## Install for your system

| You have | Run |
|---|---|
| Ubuntu 22.04+/Debian 12+ desktop, nothing installed | `./install-linux.sh --profile full --install-desktop --install-cli` |
| Same, Desktop already installed | `./install-linux.sh --profile standard` (then quit and restart Desktop once: it reads PATH at launch) |
| Fedora / RHEL / openSUSE / Arch desktop | `./install-linux.sh --install-cli` (the Desktop checks of the default profile print the community Desktop options; nothing is fetched) |
| Server, SSH box, WSL, no Desktop | `FAL_KEY=... ./install-linux.sh --profile headless --install-cli --enable-linger` (headless reads keys from the environment; add `--keys` for the prompt) |
| CI or a container | `./install-linux.sh --profile ci --allow-root --no-sudo` with `FAL_KEY` and the research keys mapped from the CI secret store (ci never prompts) |
| Smallest footprint | `./install-linux.sh --profile minimal` (c101 core plus the image preset, fal, no profile files) |
| Everything c101 can do on Linux | `./install-linux.sh --profile full` |
| Only some agents and skills | `./install-linux.sh --preset image,research` or `--pick-preset` (see Presets) |
| Change keys, nothing else | `./install-linux.sh --keys --keys-only` |
| Also expose servers in Desktop's Chat tab | add `--desktop-chat-mcp` (read the warning below first) |
| Exact model allowlist for all users | add `--managed-settings` (writes `/etc/claude-code/managed-settings.json`) |
| NixOS | needs `programs.nix-ld.enable = true` and nixpkgs' `uv` first: the installer checks, prints the module snippet and stops otherwise ([SCENARIOS](docs/SCENARIOS.md)) |
| Silverblue / SteamOS | see [SCENARIOS](docs/SCENARIOS.md): packages are printed, not layered |

## What else works on Linux

* **Research.** The `deep-researcher`, `web-searcher`, `web-crawler` and `web-scraper` agents and the `research-sweep`
  workflow use exa, jina, spider, fetch and context7. Doctor: `keys-research` and `mcp-fetch-cache`.
* **Presets.** Named sets of c101 packs (see Presets) or plain `--packs`.
* **Swarms and workflows.** c101 links its nine workflows (swarm-run, fanout, mediate, expert-panel, plan-tournament,
  research-sweep, review-verify, visual-iterate, manuscript-review); doctor `workflows-linked`. Its `settings.json` sets the
  limits: spawn depth 8, 20 concurrent subagents, 16 concurrent workflow agents (doctor `settings-env`), and the policy
  allows 256 spawns per session. None of this depends on the OS.
* **Delegator.** `--main-agent` (profiles standard, full, headless) makes the delegator the main agent; doctor `main-agent`.
  `claude-stack` (in `~/.local/bin`) starts it pinned to its tier model and needs the `claude` CLI.
* **Statistics.** Spawns per session by depth and agent type (`c101-stack depth`, doctor `depth-stats`), the spawn and
  stop ledger, token budget, milestones (`c101-stack progress -f`), and the learner's history (`c101-stack learn report`).
  `c101-stack` is c101's `bin/stack`, linked into `~/.local/bin` by standard, full and headless; elsewhere run
  `~/.claude/stack/bin/stack`.
* **maxTurns and effort, adjusted on the fly.** c101's learner rewrites `maxTurns`, `effort` and `autoCompactWindow` in agent
  files from what past runs needed, at SessionEnd. `c101-stack agents set NAME maxTurns=80 effort=high` does it by hand.
  Both write the agent files of the c101 checkout, which must be writable (doctor `learning-writable`,
  [PITFALLS 49](docs/PITFALLS.md) and 59).
* **Learning backstop.** On Linux, quitting Desktop or logging out may skip SessionEnd and lose that session's statistics.
  `--learn-timer` (on in standard, full, headless) installs a systemd user timer that runs every 30 minutes and learns from
  sessions that ended without SessionEnd (doctor `learning-timer`, `learning-log`;
  [PITFALLS 50](docs/PITFALLS.md)). Without a systemd user manager the installer prints a cron line instead (the backstop
  needs GNU find there: `apk add findutils` on Alpine).

## Presets (`config/presets.conf`)

A preset is a named list of c101 install packs; `core` is always installed.

| Preset | Packs |
|---|---|
| `research` | core + coding-core |
| `coding` | coding-core, python, typescript, rust, cpp, go, java, kotlin, haskell, julia, devops, containers |
| `image` | image, graphic-design |
| `science` | mathematics, physics, quantum, computer-science, computer-graphics, julia |
| `writing` | writing |
| `devops` | devops, containers, python |
| `everything` | every pack (the default of standard, full and headless) |

* **Selection rule.** `--preset` (repeatable), the presets chosen with `--pick-preset` and `--packs` are unioned into one
  command-line selection. That selection **replaces** the profile's `PRESET` and `C101_PACKS`; without one the profile
  applies (minimal and ci: `image`; standard, full, headless: `everything`).
* `everything`, or the pack name `all`, selects every pack.
* The fal backend always adds the `image` preset (image + graphic-design); with every pack selected that changes nothing.
* c101 adds each pack's own requirements (graphic-design needs image; python, typescript and devops need coding-core).
* Unknown names stop the installer with exit 2. `--print-config` shows the resolved `PRESET` and `PACKS` before anything
  changes.

## Profiles (`config/profiles/*.conf`)

| Profile | c101 flags and packs | Overlay steps |
|---|---|---|
| `minimal` | `--user`, core + image + graphic-design (preset image) | fal, fal-runner, MCP pre-warm, Desktop checks; no profile files, no command links, no systemd units |
| `standard` (default) | `--user --main-agent`, all packs | + login-profile PATH block, environment.d on systemd sessions, `~/.local/bin` command links, Playwright's Chromium, learning timer, Desktop checks |
| `full` | `--user --main-agent --enforce-models`, all packs | standard + rootless Podman, desktop-sandbox image, Podman systemd units, Playwright system libraries |
| `headless` | `--user --main-agent`, all packs | keys from the environment, PATH block for SSH/login shells, command links, Chromium, learning timer; no Desktop checks, no environment.d |
| `ci` | `--user`, core + image + graphic-design (preset image) | `--yes --no-sudo`, keys from the environment only, no pre-warm, Chromium or timer, no Desktop checks, no profile files |

The seven profile keys added by the parity change:

| Key | minimal | standard | full | headless | ci |
|---|---|---|---|---|---|
| `PRESET` | image | everything | everything | everything | image |
| `KEYS` | auto | auto | auto | env | env |
| `IMAGE_BACKEND` | fal | fal | fal | fal | fal |
| `FAL_MCP` | 1 | 1 | 1 | 1 | 1 |
| `PREWARM_MCP` | 1 | 1 | 1 | 1 | 0 |
| `PLAYWRIGHT_BROWSER` | 0 | 1 | 1 | 1 | 0 |
| `LEARN_TIMER` | 0 | 1 | 1 | 1 | 0 |

`KEYS=auto` imports keys from environment variables of the same names, then asks for the missing ones when stdin is a
terminal and `--yes` is not set; `env` never prompts. `KEYS_OVERWRITE` is never a profile key (only `--keys-overwrite`).
A profile file of your own that lacks these keys gets `KEYS=auto`, `IMAGE_BACKEND=fal`, `FAL_MCP=1`, `PREWARM_MCP=1`,
`PLAYWRIGHT_BROWSER=0`, `LEARN_TIMER=0` and an empty `PRESET` (its `C101_PACKS` decides).

Command-line flags override a profile. **A re-run without `--profile` keeps the profile the state file recorded** (one info
line says so); an explicit `--profile` wins. Every re-run command the installer and the doctor print carries `--profile NAME`
unless it is standard, so following a hint never turns a minimal, headless or ci host into a standard one.
`./install-linux.sh --profile full --print-config` prints the resolved plan,
including `PRESET`, `PACKS`, `KEYS`, `KEYS_OVERWRITE`, `IMAGE_BACKEND`, `FAL_MCP`, `PREWARM_MCP`, `PLAYWRIGHT_BROWSER` and
`LEARN_TIMER` (names and fixed words, never a key).

## Flags

New with the parity change (11):

| Flag | Meaning |
|---|---|
| `--keys` | ask (hidden input) for each missing key on a terminal, even with `--yes`: `FAL_KEY` first, then the research keys, then the extras |
| `--keys-from-env` | keys only from environment variables of the same names, never a prompt |
| `--keys-only` | only seed `stack.env` (keys and image backend), then exit: no c101 installer, no other step |
| `--keys-overwrite` | replace values already in `stack.env`, after a mode-600 backup; the only flag that does (`--force` never touches `stack.env`) |
| `--image-backend fal\|none\|keep` | fal is every profile's default; `none` opts out and is sticky; `keep` leaves the image settings as they are |
| `--fal-mcp` / `--no-fal-mcp` | the `fal-runner` agent and `~/.config/c101/fal-mcp.json` (default on); never a user-scope registration |
| `--preset LIST` | `research`, `coding`, `image`, `science`, `writing`, `devops`, `everything`; repeatable, unioned with `--packs` |
| `--pick-preset` | choose presets from a numbered menu (needs a terminal; not with `--keys-only`) |
| `--prewarm-mcp` / `--no-prewarm-mcp` | download fetch and neural-memory now (plus `@playwright/mcp` with the browser, markitdown when in `--mcp`) so their first start does not time out; needs uv |
| `--playwright-browser` / `--no-playwright-browser` | Playwright's own Chromium at user level (no root) and `PLAYWRIGHT_MCP_BROWSER=chromium` in the login profile; Playwright MCP's default Chrome channel rarely exists on Linux and never on arm64 |
| `--learn-timer` / `--no-learn-timer` | the systemd user timer that runs c101's learning for sessions that ended without SessionEnd |

All flags:

| Flag | Meaning |
|---|---|
| `--profile NAME` | `minimal`, `standard` (default), `full`, `headless`, `ci` |
| `--print-config` | print the resolved plan as KEY=VALUE and exit (no changes) |
| `--source PATH` / `--repo URL` / `--ref REF` | the c101 checkout, or what to clone (default origin `https://github.com/rustcircle/c101.git`, ref `main`, into `~/.local/share/c101-linux/c101`) |
| `--packs a,b` | c101 install packs (`core` always included); unioned with `--preset` |
| `--mcp a,b` | optional servers from c101's `config/mcp.optional.json`, passed through; Adobe, `peekaboo` and the hosted `fal` / `fal-oauth` are refused (c101 registers those at user scope, which would load fal's tools in every session: use the `fal-runner` agent or `claude --mcp-config ~/.config/c101/fal-mcp.json`). The research servers are always registered |
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
| `--playwright-deps` | Chromium's system libraries through Playwright's own installer (Debian/Ubuntu, root) |
| `--path-integration` / `--no-path-integration`, `--environment-d` / `--no-environment-d` | PATH files |
| `--no-sudo`, `-y/--yes`, `-n/--dry-run`, `--allow-root`, `-h/--help` | behaviour. Under `--no-sudo` (or without sudo) every root command is printed **and** written to `~/.local/state/c101-linux/pending/pending-root-commands.sh`, with the files it needs next to it, so you can review it and run `sudo sh` on it after the installer has exited |
| `--adobe`, `--no-adobe` | **refused**: Adobe apps do not exist on Linux (see the parity table) |
| the 11 flags above | keys, fal, fal-runner, presets, pre-warm, Chromium, learning timer |

Exit codes: 0 ok, 1 failure, 2 usage, 3 refused (not Linux, or root without `--allow-root`).

`--dry-run` changes nothing and prints `would set NAME` for keys, never a value. It also reaches c101: when the checkout
already has a `.venv`, c101's own entry point (`python -m stack.install --dry-run`, exactly what `install.sh` execs) runs
from it; otherwise the command is printed. c101's `install.sh --dry-run` itself would create `.venv` and the uv cache,
which a dry run must not do.

**bash >= 4.2** is required: `lib/keys.sh` uses `declare -g` (bash 4.2+), and the installer stops with exit 2 on an older
bash. The doctor and the uninstaller need bash >= 4.

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

Units live in [`systemd/user/`](systemd/user) and are installed to `~/.config/systemd/user/` (removed by the uninstaller):

| Unit | What it does |
|---|---|
| `c101-podman-api.socket` / `.service` | fallback Docker-compatible API at `%t/podman/podman.sock` (`podman system service --time=0`), installed only when the distro has no `podman.socket` (`--profile full` or `--podman-services`) |
| `c101-sandbox-image.service` | `Type=oneshot`: `desktop-sandbox --build --profile recommended --base docker.io/library/debian:bookworm-slim` with `DESKTOP_SANDBOX_RUNTIME` pointing at podman |
| `c101-sandbox-image.timer` | weekly refresh of that image (enabled with `--podman-timers`) |
| `c101-podman-prune.service` / `.timer` | weekly, scoped to c101: `podman container prune -f --filter label=dev.c101.desktop-sandbox=1 --filter until=168h` and `podman image prune -f --filter label=dev.c101.desktop-sandbox.packages`. Your other stopped containers, images, networks and all volumes are untouched (enabled with `--podman-timers`) |
| `c101-learn-backstop.service` / `.timer` | every 30 minutes (`Persistent=true`): `~/.local/bin/c101-learn-backstop` runs c101's `learn run` for sessions that missed SessionEnd (`--learn-timer`; standard, full, headless) |

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
  Code tab shares (settings, hooks, skills). Whether it also loads `~/.claude/agents` is not confirmed by Anthropic's docs
  (so `fal-runner` and the other agents in the Code tab are unverified, PITFALLS 15).
* **Why the stack's MCP servers are not put into `claude_desktop_config.json` by default:** the Code tab loads that file
  too, so they would start eagerly in every Code session instead of lazily inside the agents that need them (c101's
  design), and `neural-memory` would get a second SQLite writer. `--desktop-chat-mcp` is the opt-in, with
  `c101-fetch`, `c101-playwright` (with `--browser chromium`), `c101-image`, `c101-desktop-sandbox` (stdio only; the HTTP
  servers have no place in that file and `neural-memory` is left out on purpose). Keys stay in `~/.claude/stack.env`,
  read by `bin/mcp-run`; those servers pick up an edit only after Desktop is quit completely and started again.
* Desktop reads the login shell's PATH (and `PLAYWRIGHT_MCP_BROWSER`) once at start: restart it after the installer changed
  the profile.
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
systemd units (the learning timer included), installed commands (`c101-learn-backstop` included) and the backstop's stamps, the
`fal-runner` link and `~/.config/c101/fal-mcp.json` while they are still the installed ones (an edited config is kept with a
warning), the managed-settings keys, calls c101's `--no-main-agent`, `--no-enforce-models`
and `--no-python-default` (only for what was enabled), strips c101's hooks from `~/.claude/settings.json` (otherwise
they would fail on every session once the link is gone), removes the `settings.json` env keys c101 added (compared with a snapshot taken
before c101 first ran), runs `claude mcp remove --scope user` for servers registered through `--mcp`, removes the
cgroup-delegation drop-in it wrote, removes the `pending/` leftovers of earlier deferred root commands (a new `pending-root-commands.sh` appears only when this uninstall itself had to defer something), and removes `~/.claude` symlinks that point into the c101 checkout.

It **keeps `~/.claude/stack.env` and its `.bak-*` backups** (your keys; delete old backups by hand), `~/.claude/stack-data`,
Playwright's Chromium (`~/.cache/ms-playwright`), the uv and npm caches the pre-warm filled, uv, Node, packages, the
container image and linger, and prints the commands or paths for those. When `stack.env` is missing it skips the c101
reversal (c101's installer would recreate the file) and prints what to undo by hand ([PITFALLS 58](docs/PITFALLS.md)).
After a `--keys-only` run alone it leaves c101's links and hooks untouched.

## Doctor

`bin/c101-linux-doctor [--profile NAME] [--skip-c101] [--offline] [--quiet]` is read-only and prints
`PASS/WARN/FAIL/INFO` lines in sections, in this order:

| Section | Check ids |
|---|---|
| Image generation (fal) | `stack-env`, `stack-env-mode`, `keys-image` (one line per operation), `image-router`, `image-deps`, `fal-runner`, `fal-mcp-config`, `fal-mcp-reach` (an HTTPS probe that sends no credentials) |
| Research servers | `keys-research` (exa, jina, spider, context7), `mcp-fetch-cache` |
| MCP runtime | `playwright-browser`, `neural-memory-cache` |
| Hooks | `hooks-events`, `hooks-runtime` |
| Workflows | `workflows-linked` |
| Agents and skills | `agents-linked` |
| Settings | `settings.json`, `settings-env`, `main-agent`, `enforce-models` |
| Learning | `learning-config`, `learning-writable`, `learning-timer`, `learning-log`, `depth-stats` |
| Platform | uv and Python 3.13, node/npx, claude CLI or Desktop, the `~/.claude/stack` link, the login-profile block and whether a *fresh login shell* finds `uv`, Desktop config JSON, keyring, podman version/rootless/subuid/storage driver, the sandbox image, the Podman API socket, KVM, Wayland notes |

Then it runs c101's `bin/stack doctor` (`--skip-c101` skips it). Profile-dependent verdicts use the profile recorded in the
state file unless `--profile` is given; `--offline` skips the network probe, `--quiet` prints only WARN and FAIL lines and
the totals. Key checks read `stack.env` only through `lib/stackenv.py` and print names, never values. Exit 0, 1 on any
FAIL, 2 usage, 3 not Linux.

## Tests (run on Linux; nothing here is meant to run on macOS)

```bash
tests/run-tests.sh                 # shellcheck, bash -n, py_compile, then the unit tests (uv's Python 3.13 when uv exists)
tests/run-tests.sh -k keys         # a subset
tests/matrix.sh                    # distro matrix in containers, rootless podman first
CONTAINER_RUNTIME=docker tests/matrix.sh ubuntu:24.04      # docker only when asked for
C101_LINUX_RUNTIME_TESTS=1 python3 -m unittest discover -s tests -p 'test_linux_runtime.py' -v   # after a real install
```

14 test modules with 395 test functions (`grep -c 'def test_' tests/*.py`) build a throw-away HOME and TMPDIR. They cover:

* profile resolution and the new profile keys, presets and pack selection;
* the key step (with a sentinel value that must never reach argv, output or the state file) and `stackenv.py` merges;
* the fal preset, the pre-warm and Chromium commands, and the learning backstop's transcript selection;
* the doctor's new checks;
* PATH block idempotence, per-shell target files, nvm resolution;
* JSON merge idempotence, backup, invalid JSON and running-app refusal;
* dry runs changing nothing (with a fake `podman` and on a host without Python), symlinked dotfiles, deferred root commands;
* root and Adobe refusal, and uninstall (links, hooks, env keys, MCP servers, system files, the timer, the fal-runner pieces,
  `stack.env` kept byte for byte);
* the `c101-podman` wrapper and the systemd unit files, cgroup delegation, the DOCKER_HOST decision, the NixOS preflight
  and distro detection.

`tests/test_linux_runtime.py` holds the Linux-only checks of the runtime doubts and is skipped unless
`C101_LINUX_RUNTIME_TESTS=1` ([VERIFICATION](docs/VERIFICATION.md) section 5). The matrix runs `ubuntu:24.04`,
`ubuntu:22.04`, `debian:12`, `fedora:41`, `archlinux:latest` (skipped cleanly on arm64) and `alpine:3.20`: install,
install again, doctor, a dry run that must change nothing, uninstall. It mounts both repositories read-only, works on
copies (c101's installer needs a writable tree) and passes fake keys.

Test hooks (environment variables; not for normal use): `C101L_PY` (helper interpreter), `C101L_STATE_FILE`,
`C101L_PRESETS_FILE`, `C101_LINUX_FAKE_EUID` (root-refusal test as a normal user), `C101_LINUX_OS_RELEASE`, `C101_LINUX_UNAME_M`,
`C101_LINUX_LOGIN_SHELL`, `C101_LINUX_DESKTOP_PATTERN`, `C101_LINUX_OSTREE_BOOTED` (0/1), `C101_LINUX_CGROUP_FILE`,
`C101_LINUX_DOCKER_SOCKETS`, `C101L_SKIP_NIXOS_CHECK=1` (continue on NixOS without the nix-ld/uv preflight), `C101L_UV_VERSION`.

## Layout

```
install-linux.sh        entry point (flags, profiles, step order)
uninstall-linux.sh      reverse of the above, driven by the state file
bin/c101-linux-doctor   read-only health check
bin/c101-podman         podman wrapper installed as ~/.local/bin/c101-podman
bin/c101-learn-backstop learning backstop, installed as ~/.local/bin/c101-learn-backstop
agents/fal-runner.md    the hosted fal MCP as a lazy agent (linked into ~/.claude/agents)
config/profiles/*.conf  profile -> c101 flags, presets, keys and overlay steps
config/presets.conf     named presets -> c101 packs
config/fal-mcp.json     whole-session fal MCP config (copied to ~/.config/c101/)
config/desktop-mcp.json curated server set for --desktop-chat-mcp
lib/*.sh                detect, deps (uv, node, podman), c101 (fetch/run/links), desktop, pathprofile, services, profiles,
                        keys, presets, prewarm, learning, falmcp, common
lib/*.py                stdlib-only helpers (state, managed blocks, Desktop JSON merge, settings edits, stack.env, packs)
systemd/user/*          Podman API socket, sandbox image refresh, prune, learning backstop units and timers
tests/                  unit tests, run-tests.sh, matrix.sh, os-release fixtures
docs/                   SCENARIOS, PITFALLS, UPSTREAM, OUTCOME, VERIFICATION
```

Documentation: [scenarios and the feature parity table](docs/SCENARIOS.md) | [pitfalls](docs/PITFALLS.md) (keys, fal,
pre-warm and learning: 43 to 59) | [changes that belong upstream](docs/UPSTREAM.md) (13 to 26 for the parity change) |
[outcome](docs/OUTCOME.md) | [verification record](docs/VERIFICATION.md)

State lives in `~/.local/state/c101-linux/state.json` (mode 0600): profile, c101 source and flags, preset, packs, image
backend, edited files, installed units and files, apt files, Desktop entries added; never a key name or value. Every edit
of an existing user file writes `<file>.bak-<UTC stamp>` first.
