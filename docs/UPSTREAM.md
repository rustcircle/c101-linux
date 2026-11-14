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
