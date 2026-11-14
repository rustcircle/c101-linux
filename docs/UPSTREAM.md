# Changes that belong upstream (c101)

c101Linux never edits c101. Everything below is worked around in the overlay; each item says where and what a patch
would look like, so the workaround can be deleted once upstream takes it. Line numbers refer to c101 at commit
`7b37043` (https://github.com/rustcircle/c101, checked 2026-11-14). The suggested diffs are not applied anywhere.

Priority: **P1** breaks a Linux feature, **P2** costs Linux users time, **P3** wording or hygiene.

## 1. Launcher PATH lists know macOS and nix, not Linux (P1)

| File:line | Today |
|---|---|
| `install.sh:14` | `~/.local/bin`, nix profiles, `/opt/homebrew/bin`, `/usr/local/bin` |
| `bin/_pyenv.sh:15` | `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` |
| `bin/mcp-run:18` | `~/.local/bin`, nix profiles, `/opt/homebrew/bin`, `/usr/local/bin` |
| `bin/neural-memory-mcp:18` | `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` |
| `stack_mcp/desktop_sandbox/config.py:27` (`EXTRA_PATHS`) | `/opt/homebrew/bin`, `/usr/local/bin`, `~/.orbstack/bin`, Docker.app |

A GUI-launched Claude Desktop does not read `~/.bashrc`, so `uv`, `npx` and the container CLI are missing for hooks and MCP
servers unless the login profile exports them (the overlay does that). Missing on Linux: `~/.cargo/bin`,
`/home/linuxbrew/.linuxbrew/bin`, `~/.linuxbrew/bin`, `/snap/bin`, `~/.npm-global/bin`, `~/.bun/bin`, and the version managers
(`~/.volta/bin`, `~/.local/share/fnm/aliases/default/bin`, `${ASDF_DATA_DIR:-~/.asdf}/shims`, `~/.local/share/mise/shims`, nvm's default node).

Suggested: one `bin/_path.sh` (a single list, sourced by the four launchers) and the same list in `EXTRA_PATHS`:

```diff
+# bin/_path.sh (new): one place for the PATH fallback
+_c101_extra="$HOME/.local/bin:$HOME/.nix-profile/bin:/etc/profiles/per-user/${USER:-nobody}/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin"
+_c101_extra="$_c101_extra:$HOME/.cargo/bin:$HOME/.volta/bin:$HOME/.local/share/mise/shims:${ASDF_DATA_DIR:-$HOME/.asdf}/shims"
+_c101_extra="$_c101_extra:$HOME/.bun/bin:$HOME/.npm-global/bin:/home/linuxbrew/.linuxbrew/bin:/snap/bin:/opt/homebrew/bin:/usr/local/bin"
+export PATH="$_c101_extra:${PATH:-/usr/bin:/bin}"
```
```diff
 # stack_mcp/desktop_sandbox/config.py:27
-EXTRA_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.orbstack/bin"),
-               "/Applications/Docker.app/Contents/Resources/bin")
+EXTRA_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin", "/snap/bin",
+               os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.orbstack/bin"),
+               "/Applications/Docker.app/Contents/Resources/bin")
```
Overlay workaround: managed login-profile block + `environment.d` (`lib/pathprofile.sh`).

## 2. `install.sh` hints and checks assume Homebrew and any node (P2)

* `install.sh:19` prints `brew install uv` first. On Linux the hint should be the distro-neutral astral installer (already on the next line) plus
  `pacman -S uv` / `apk add uv` / `nix profile install nixpkgs#uv`. An `uname -s` switch is enough.
* `install.sh:42` only tests that `node` exists. Ubuntu 22.04 ships Node 12, which passes the test and then fails at the first `npx @playwright/mcp`.
  Suggested: `node -e 'process.exit(+process.versions.node.split(".")[0] < 18)'` and say "Node 18+ with npx". Overlay: `lib/deps.sh` `node_state`.

## 3. `install.sh --dry-run` is not a dry run (P2)

`install.sh:39` skips `uv python install`/`uv sync`, but `install.sh:44` still `exec uv run --project "$REPO"`, which creates or syncs `.venv`
and the uv cache (documented in `docs/reference/installation.md`). Suggested: in dry-run mode `exec "$REPO/.venv/bin/python" -B -m stack.install "$@"` when
that interpreter exists, or `uv run --no-sync`, and only print otherwise. Overlay: `lib/c101.sh` `c101_exec` does exactly that for `--dry-run`.

## 4. The sandbox does not work under rootless podman as written (P1 for podman users, *unverified at runtime*)

* `stack_mcp/desktop_sandbox/runtime.py:89` runs the container with `--user <host uid>:<host gid>`, and `runtime.py:242` creates the exchange directory with mode 0700.
  In rootless podman the host uid is container root, so uid 1000 inside is a different (subuid) user and cannot use the directory.
* `runtime.py:91-93` bind mounts carry no SELinux relabel option, which Fedora/RHEL (enforcing) reject.

Suggested (podman only; docker is unaffected):

```diff
 def run_argv(s: Settings, name: str, exchange: Path) -> list[str]:
     ...
+    podman = Path(s.runtime).name.startswith("podman") or _is_podman(s.runtime)
     return [
         s.runtime, "run", "--detach", "--rm",
+        *(["--userns=keep-id"] if podman and uid > 0 else []),
         ...
-        "--mount", f"type=bind,source={exchange},target=/exchange",
+        "--mount", f"type=bind,source={exchange},target=/exchange" + (",relabel=private" if podman else ""),
```
(`_is_podman` could inspect `<cli> --version`, since the runtime may be a wrapper.) Overlay workaround: `bin/c101-podman`, installed as
`~/.local/bin/c101-podman` and selected through `DESKTOP_SANDBOX_RUNTIME`; it adds `--userns=keep-id` and, with SELinux enforcing,
`--security-opt label=disable`. Needs confirming on a real rootless host.

## 5. Runtime auto-detection prefers docker over podman (P2)

`stack_mcp/desktop_sandbox/config.py:26` (`RUNTIMES = ("docker", "podman")`) picks docker whenever both exist, and a `podman-docker` shim is also called
`docker`. Suggested: try `podman` first when `XDG_RUNTIME_DIR` is set and the `docker` found is the podman shim (`docker --version` contains "podman"), or
document `DESKTOP_SANDBOX_RUNTIME` in `config/stack.env.example:105` as the Linux recommendation. Overlay: exports `DESKTOP_SANDBOX_RUNTIME`.

## 6. Dockerfile base image is a short name (P2)

`stack_mcp/desktop_sandbox/image/Dockerfile:12-13` (`ARG BASE=debian:bookworm-slim`). Podman cannot resolve short names without a TTY.
Suggested: `ARG BASE=docker.io/library/debian:bookworm-slim` (docker accepts it too). Overlay: always passes `--base docker.io/library/debian:bookworm-slim`.

## 7. `bin/claude-stack` does not follow symlinks (P3)

`bin/claude-stack:10` computes `here` from `dirname "${BASH_SOURCE[0]}"` while `bin/_pyenv.sh:9-13` resolves symlinks. Suggested: reuse the `_pyenv.sh` loop, so
`ln -s ~/.claude/stack/bin/claude-stack ~/.local/bin/` works. Overlay: installs exec wrappers instead of symlinks.

## 8. No uninstall path (P2)

`stack/install.py` has reversible flags for the main agent (`--no-main-agent`, :365), model enforcement (`--no-enforce-models`, :367), python default
(`--no-python-default`, :369) and Adobe (`--no-adobe`, :375), but nothing removes the hooks merged by `merge_settings` (`HOOK_MARK`, :57, :303-312), the per-file
symlinks from `link_components`, or the `~/.claude/stack` link. After deleting the checkout, every session fails its hooks. Suggested: `--uninstall`
(strip hook commands containing `HOOK_MARK`, remove symlinks into `paths.REPO`, remove the link). Overlay: `uninstall-linux.sh`.

## 9. Wording: computer use is described as macOS (P3)

* `.claude/agents/research/computer-operator.md:16, :37-38, :66` say the built-in computer-use server drives "the user's own macOS apps" and speak of the
  "host desktop" route as if it always exists. On Linux that route does not exist (no built-in server); the agent should be told to use the sandbox and to say
  so when the task needs the user's own apps.
* `docs/MCP.md:34` ("macOS, interactive sessions, Pro/Max") is accurate; add "Linux: not available, use `desktop-sandbox`".
* `docs/reference/installation.md` requirements table says "macOS or Linux" but lists no Linux specifics (Node version, container runtime, subuid ranges).

## 10. Rootless cgroup controllers (P1 on Ubuntu 22.04)

`stack_mcp/desktop_sandbox/runtime.py:86-88` always passes `--memory`, `--memory-swap`, `--cpus` and `--pids-limit`. Under rootless podman a controller that systemd has not delegated
makes crun refuse to start the container (`cpu` is not delegated by systemd 249, i.e. Ubuntu 22.04). Suggested: read the cgroup's `cgroup.controllers` (or try `<cli> run --cpus 1 ... true` once) and
omit unavailable limits with a stderr warning instead of failing every `sandbox_start`. Overlay: delegation drop-in (`lib/deps.sh` `ensure_cgroup_delegation`), doctor FAIL.

## 11. Smaller notes

* `stack/doctor.py:317-384` checks `node` only for the workflow syntax check; `npx` and the version are not checked, though `playwright` depends on them.
* `_register_user_mcp` / `register_optional` (`stack/install.py:199`, `:248`) need the `claude` CLI; a Desktop-only Linux install has none, so `--mcp` prints manual commands.
  Writing the same entries into `~/.claude.json` directly would serve Desktop-only users.
* `bin/desktop-sandbox` could mention `DESKTOP_SANDBOX_RUNTIME=podman` and the `--base` option in its `--help` for Linux.

## 12. uv-managed Python on NixOS (P2)

`install.sh:32-36` runs `uv python install 3.13`, and `stack/install.py` writes `UV_PYTHON_PREFERENCE=only-managed` into `~/.claude/settings.json`; both need a dynamically linked
Python that stock NixOS refuses to run without `programs.nix-ld.enable`. Suggested: honour an existing interpreter when `UV_PYTHON_DOWNLOADS=never` is set, and document nix-ld in
`docs/reference/installation.md`. Overlay: preflight (`nixos_check`) requires nix-ld and nixpkgs' uv.

Items 13 to 26 come from the parity change of 2026-11-14 (keys, fal, presets, MCP start-up, learning backstop), at the same
c101 commit `7b37043`. Items 22 and 23 are cross-references from the overlay's security review of its key handling; c101's
own code was read for them, not reviewed in full.

## 13. Playwright MCP has no `--browser` on Linux (P1)

`config/mcp.servers.json:4-15` starts `npx -y @playwright/mcp@latest --headless --isolated ...` with no browser setting, and
`bin/stack mcp sync` renders that into every agent that uses it. @playwright/mcp then uses the Chrome channel, which most Linux
hosts lack and arm64 Linux never has ("Chromium distribution 'chrome' is not found"). The server is started by `npx` directly,
not through `bin/mcp-run`, so a `PLAYWRIGHT_MCP_BROWSER` line in `stack.env` would not reach it either.
Suggested: start it through a small launcher (like `bin/neural-memory-mcp`) that adds `--browser chromium` on Linux unless
`PLAYWRIGHT_MCP_BROWSER` is set (`chromium` is Playwright's own Chrome for Testing build), and install the matching browser at
user level: `npx -y "playwright-core@$(npm view @playwright/mcp@latest dependencies.playwright-core)" install --no-shell chromium`
(no root; the system libraries stay a separate root step). Overlay: `PLAYWRIGHT_MCP_BROWSER=chromium` in the login-profile block
and `environment.d` (`lib/pathprofile.sh`), `--browser chromium` in `config/desktop-mcp.json`, `--playwright-browser`
(`lib/prewarm.sh`); PITFALLS 43.

## 14. Keys the stack reads but `stack keys` does not list (P3)

`config/policy.json:11-43` (`optional_keys`) lists the image keys, `CONTEXT7_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY` and
`BRAVE_API_KEY`, but not `FAL_KEY` (read by `bin/mcp-headers:52`), `REPLICATE_API_TOKEN` (`config/mcp.optional.json:23`) or
`RUNWARE_API_KEY` (`config/mcp.optional.json:43`, `config/stack.env.example:99`), so `bin/stack keys` (`stack/cli.py:164-179`)
never reports them. Suggested: add the three names. Overlay: the doctor reports `FAL_KEY` by name (`keys-image`, `fal-runner`).

## 15. `stack doctor` checks neither keys nor image operations (P2)

`stack/cli.py:145-161` runs `stack/doctor.py`, which never calls `envfile.missing` or the image router's status
(`stack_mcp/image_router/server.py:206-219`, the `--check` output). A user without a fal key gets a clean doctor and an image
server without tools. Suggested: a doctor section that prints the `stack keys` result (names only) and one line per image
operation from `status_lines()`. Overlay: `bin/c101-linux-doctor` sections "Image generation (fal)" and "Research servers".

## 16. Spawn budget: the docs say 128, the policy says 256 (P3)

`docs/DEPTH.md:23` says `max_spawns_per_session` defaults to 128; `config/policy.json:3` sets 256, and the code default when the
key is missing is 0, no budget (`stack/hooks.py:235`). Suggested: "256 in config/policy.json; no limit when the key is absent".

## 17. Stray `stack_sdk/options(1).py` (P3)

A second copy of `stack_sdk/options.py` with different content and a browser-download name. Nothing imports it (a module name
with parentheses cannot be imported), and the parentheses trip shell globs and some packaging tools. Suggested: delete it.

## 18. `learn ingest`, `update` and `apply` run without `learn.lock` (P2)

`stack/cli.py:184-200`: only `learn run` (`:201-206`) and the hooks (`stack/hooks.py:473`, `:531`) take
`stack-data/learn.lock`. A hand-run `bin/stack learn apply` (or the stack-tuner agent) can interleave with a detached SessionEnd
`learn run` on `recommendations.json`, `changes.jsonl` and the agent files. Suggested: take the same lock in all three (block, or
fail fast with a message). Overlay: the learning backstop calls only `learn run`.

## 19. No key step in the installer, and the fal preset repeats the key per operation (P2)

`stack/install.py:360-379` has no key or image-backend option; `base()` (`:84-95`) only copies `config/stack.env.example` and says
"fill in your keys". `config/stack.env.fal.example` repeats `your-fal-key` for nine operations (`:33`, `:44`, `:56`, `:66`, `:73`,
`:80`, `:88`, `:102`, `:113`) and has a separate `FAL_KEY=` (`:122`), because none of the four readers (`stack/envfile.py`,
`bin/mcp-run`, `bin/mcp-headers`, `stack_mcp/image_router/config.py`) expands variables; rotating a key means editing ten lines.
Suggested: let the router fall back to `FAL_KEY` when an operation's URL host is `queue.fal.run` and its KEY is empty, so one line
holds the key; and an installer option that merges the fal preset with one hidden prompt (stdin only, symlink-aware atomic write,
backup first). Overlay: `lib/stackenv.py merge --preset` and `lib/keys.sh` (`--keys`, `--keys-from-env`, `--keys-only`,
`--keys-overwrite`, `--image-backend`); PITFALLS 46 to 48.

## 20. `bin/stack mcp enable` works at project scope only (P2)

`stack/cli.py:332-381` writes `.mcp.json` and `.claude/settings.json` inside the checkout. After `install.sh --user` the checkout
is `~/.claude/stack`, so `bin/stack mcp enable fal`, which `config/stack.env.example:93` recommends, edits tracked files, and the
server appears only in sessions whose project is the checkout. Suggested: `--scope user` that goes through `_register_user_mcp`
(`stack/install.py:199`) with the `_user_mcp_entry` path rewrite, or an agent that carries the server inline (item 21).
Overlay: the `fal-runner` agent and `~/.config/c101/fal-mcp.json`; PITFALLS 52.

## 21. Registrations c101 would need for an agent like `fal-runner` (P2)

The overlay ships `agents/fal-runner.md` (c101's conventions, the frontmatter order of `image-editor`) and changes nothing in c101.
To make it a first-class agent c101 would need:
* `config/models.json`, next to `image-editor` (`:288`): `"fal-runner": {"tier": "MEDIUM_MODEL", "effort": "medium",
  "maxTurns": 120, "why": "fal model catalog, video, audio and 3D runs and jobs through the hosted fal MCP"}`. Without it
  `bin/stack agents tiers --apply` does not manage its model (the file names `claude-sonnet-5-5`).
* `config/learning.json` `agents` (`:60`): `"fal-runner": {"maxTurns": [30, 300], "effort": ["low", "high"]}`. Without it the
  learner applies `default_bounds` (`:45`: maxTurns 5 to 1000, effort up to max) to an agent that spends money per call, and
  writes the result into the overlay's file (PITFALLS 59).
* `config/policy.json` `spawn_rules.callers` (`:82`): `"fal-runner": ["file-summarizer", "quick-researcher", "visual-reviewer"]`,
  like the `image-editor` row (`:113`); callers that are not listed may spawn anything.
* A routing row and roster entries in `.claude/agents/orchestration/delegator.md` (`:65`, `:141`) and the orchestrator's roster,
  so requests for video, audio or 3D reach it, and a row in `docs/AGENTS.md` (`:25` is the `image-editor` row).
* A `fal` server in `config/mcp.servers.json` (with the fixed helper path of item 22), so `bin/stack mcp sync` and the doctor's
  drift check know the inline copy; today neither does.

## 22. Helpers and launchers look for `bin/` in the project directory (P2, security)

`config/mcp.servers.json:20`, `:25`, `:30`, `:35` (and the stdio launchers, such as `:42`) render
`d="${CLAUDE_PROJECT_DIR:-$PWD}"; [ -x "$d/bin/mcp-headers" ] || d="$HOME/.claude/stack"; exec "$d/bin/mcp-headers" exa` into
about 30 agent files. It serves work inside the c101 checkout, but a `--user` install links the same agents into
`~/.claude/agents`, and then the helper runs any executable `bin/mcp-headers` (or `bin/mcp-run`) of the directory it resolves to,
with full access to `stack.env`. The overlay's security review rated it Low for linked agents on current Claude Code (their
helpers run in `~/.claude`, and `CLAUDE_PROJECT_DIR` is not documented for them) and Medium for a `--mcp-config` file, whose helper
runs in the session's working directory. Not settled: whether `CLAUDE_PROJECT_DIR` is inherited (a nested `claude` started from a
hook), and which working directory an agent's stdio servers get (*unverified*). `_user_mcp_entry` (`stack/install.py:191-196`)
rewrites `${CLAUDE_PROJECT_DIR:-$HOME/.claude/stack}` to the absolute link for user scope, but not this form.
Suggested: render user-scope agents with a fixed path (`exec "$HOME/.claude/stack/bin/mcp-headers" exa`), or use the project's
`bin/` only when it is the stack checkout itself (`[ "$d" -ef "$HOME/.claude/stack" ]`). Overlay: `config/fal-mcp.json` and
`agents/fal-runner.md` both use the fixed path (the install is always user level, so the agent never needs a project's `bin/`).

## 23. `bin/mcp-run` exports every name in `stack.env` and trusts the file (P2, security)

`bin/mcp-run:34-55` exports every `NAME=value` line (any name matching `:42`) into the server's environment unless `--only`
limits it, so `LD_PRELOAD`, `NODE_OPTIONS`, `PYTHONSTARTUP`, `BASH_ENV` or `PATH` in the file means code execution in every stdio
server. Neither `bin/mcp-run` (`:34`) nor `bin/mcp-headers` (`:18`) checks that the file is owned by the user and not writable by
others, so whoever controls the file or its directory (a `CLAUDE_STACK_ENV` in a shared directory, a link into a group-writable
tree) controls the servers. The readers also disagree on duplicates: `bin/mcp-run` keeps the first non-empty assignment (`:52`),
while `bin/mcp-headers` (`:15-37`, whose comment promises the same rules) and `stack/envfile.py` take the last; and in
`bin/mcp-run` a variable already in the environment beats the file unless `STACK_ENV_OVERRIDE=1`.
Suggested: `--only` with the server's own names for every launch, or at least a deny-list (`LD_*`, `DYLD_*`, `NODE_OPTIONS`,
`PYTHON*`, `BASH_ENV`, `ENV`, `PATH`, `HOME`); refuse a file or directory that another account owns or can write; one duplicate
rule in all four readers. Overlay: `lib/stackenv.py` reports directory problems, keeps one assignment per name, checks that every
value reads back identically under all four readers, and tightens the mode to 600; PITFALLS 45, 57.

## 24. `install.py` runs `base()` on every invocation, undo flags included (P2)

`stack/install.py:389` calls `base()` (`:84-95`) before any flag is looked at, so the undo flags `--no-main-agent`,
`--no-enforce-models` and `--no-python-default` relink `~/.claude/stack`, create `~/.claude/stack.env` from the template when it is
missing and create `~/.claude/stack-data`. An uninstaller that lets c101 undo its settings therefore recreates a keys file the user
removed. `base()` also ignores `CLAUDE_STACK_ENV`: it creates and tightens only `~/.claude/stack.env` (`:86`), while every reader
honours the variable (`stack/envfile.py:19`). Suggested: skip `base()` when only undo flags are given (no `--user`, `--mcp` or
`--adobe`), and use `envfile.env_path()`. Overlay: `uninstall-linux.sh` skips the c101 reversal when `~/.claude/stack.env` is
missing and prints the manual undo; PITFALLS 58.

## 25. The learner rewrites tracked files in the checkout (P3)

`stack/learn/engine.py:342-391` writes recommendations into agent frontmatter, `stack/frontmatter.py:142-153` follows links to
their target, and `stack/paths.py:22-34` also searches `~/.claude/agents`. Consequences: a git checkout that is always dirty
(`git pull` refuses), silent failure on a read-only checkout (only `stack-data/hook-errors.log` or `learn.log` say so), and overlay
agents such as `fal-runner` tuned with default bounds and rewritten inside another repository. Suggested: keep learned values in
`stack-data` and apply them when agents are rendered or linked, or limit `apply` to files under the checkout plus agents listed in
`config/learning.json`; report failed writes in `stack doctor`. Overlay: doctor `learning-writable`; PITFALLS 49, 59.

## 26. Smaller notes (keys and session hooks)

* `stack/hooks.py:546`: the SessionStart line always names `~/.claude/stack.env`, even when `CLAUDE_STACK_ENV` points elsewhere;
  `envfile.env_path()` knows the real path.
* `config/stack.env.example:93` sends `FAL_KEY` users to `bin/stack mcp enable fal`, the project-scope route of item 20.
* `stack/hooks.py:496-503`: a session's history row and the session-end apply depend on SessionEnd alone. SessionStart catches up
  the apply (`_apply_learned`, `:514-539`) but not the history row of a session that never ran SessionEnd; a catch-up
  `learn run` for unlearned transcripts at SessionStart would make the overlay's backstop timer (PITFALLS 50) unnecessary.
