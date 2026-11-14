"""Shared helpers for the c101-linux tests. Run them on Linux: the installer refuses other platforms.

Every test works inside a throw-away HOME and TMPDIR; nothing touches the real home directory or the network.

The parity fixtures (keys, presets, pre-warm, learning backstop, doctor) are in the constants block below the imports
and in the second half of this module:
  stub c101 tree ..... make_stub_c101 / build_stub_c101, stub_calls, stack_calls, configure_stack_stub, parse_nm_pin
  argv-logging stubs . make_argv_stub, Sandbox.argv_stub / argv_stubs, ArgvStub, rule
  stack.env fixtures . write_stack_env, read_stack_env, parse_stack_env_text, file_mode, backups_of, fal_preset_values
  leak assertions .... SECRET_SENTINEL, sentinel_for, find_secret_leaks, assert_no_secret_leak, Sandbox.assert_no_secret_leak
Everything in here is fake: no value in this module is, or resembles, a real key, token or account.
"""
from __future__ import annotations

import fnmatch
import inspect
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
LINUX = sys.platform.startswith("linux")
needs_linux = unittest.skipUnless(LINUX, "the installer and its shell libraries target Linux (run these tests on Linux)")

# --- constants of the parity fixtures (fake values only) -------------------------------------------------------

# A fake, obviously-not-a-key value. Tests hand it to the installer as every key (env var, stdin) and then prove it
# shows up nowhere except the stack.env being provisioned: not in stdout, stderr, a child's argv, a unit, the state
# file, environment.d, the login-profile block, settings.json or a desktop config. It has no whitespace, quote or
# `#`, so stack.env keeps it unquoted.
SECRET_SENTINEL = "c101l-SENTINEL-NOT-A-KEY-7f3a91"

# Files under HOME that legitimately hold a key after provisioning (relative globs, for scan_tree / find_secret_leaks):
# stack.env itself and its mode-600 backups (`stack.env.bak-<UTC>`). A symlinked stack.env needs its target allowed too
# (see env_file_allowances).
STACK_ENV_ALLOW: Tuple[str, ...] = (".claude/stack.env", ".claude/stack.env.bak-*")

NM_VERSION = "4.62.0"  # the neural-memory pin in the stub c101's bin/neural-memory-mcp (the real one is a NM_VERSION="..." line)
FAL_URL = "https://queue.fal.run"
FAL_PLACEHOLDER = "your-fal-key"  # what config/stack.env.fal.example holds in every IMAGE_<OP>_KEY
STACK_STUB_SUBCOMMANDS = "keys|depth|learn"  # bin/stack stub subcommands that get canned answers (every call is logged)
STUB_LOG_NAMES = {"stack": "stack-calls.jsonl", "image-router": "image-router-calls.jsonl"}  # inside the stub c101 root

# Image operations in c101's order (stack_mcp/image_router/config.py OP_SPECS): op, purpose, MCP tool, model example.
_OP_LAYOUT: Tuple[Tuple[str, str, str, str], ...] = (
    ("GEN", "text-to-image", "image_generate", "e.g. fakeco/image-gen-1.0/text-to-image"),
    ("REF", "generation with reference images", "image_generate_with_references", "e.g. fakeco/image-gen-1.0/edit-multi"),
    ("EDIT", "instruction edits / masked inpaint", "image_edit", ""),
    ("TRANSPARENT", "transparent (RGBA) generation", "image_generate_transparent", "e.g. fakeco/image-gen-1.0/transparent"),
    ("SEGMENT", "segmentation: prompt -> mask PNG", "image_segment", "e.g. fakeco/segment-3/image (fal)"),
    ("BG_REMOVAL", "background removal -> RGBA cutout", "image_remove_background", "e.g. fakeco/background/remove (fal)"),
    ("UPSCALE", "upscaling", "image_upscale", "e.g. fakeco/upscale/image (fal), or IMAGE_UPSCALE_API=local"),
    ("VECTOR", "native SVG generation", "vector_generate", "e.g. fakeco/vector-4.5"),
    ("TRACE", "raster -> vector tracing", "image_vectorize", "e.g. fakeco/recraft/vectorize (fal), or IMAGE_TRACE_API=local"),
)

# The fal preset (stack.env.fal.example): op, fake fal endpoint id, optional fixed params (a JSON object kept in single
# quotes in the file). Nine ops get URL, KEY, MODEL; VECTOR_MANIPULATION stays `claude`; FAL_KEY (hosted fal MCP) is separate.
FAL_OP_TABLE: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("GEN", "fakeco/image-gen-1.0/text-to-image", '{"quality":"high"}'),
    ("REF", "fakeco/image-gen-1.0/edit-multi", '{"quality":"high"}'),
    ("EDIT", "fakeco/image-gen-1.0/edit", '{"quality":"high","reasoning_effort":"low"}'),
    ("TRANSPARENT", "fakeco/image-gen-1.0/transparent", '{"quality":"xhigh"}'),
    ("SEGMENT", "fakeco/segment-3/image", None),
    ("BG_REMOVAL", "fakeco/background/remove", None),
    ("UPSCALE", "fakeco/upscale/image/precision", '{"upscale_factor":2.5}'),
    ("VECTOR", "fakeco/vector-4.5", '{"strategy":"layerize","quality":"medium"}'),
    ("TRACE", "fakeco/recraft/vectorize", None),
)
FAL_OPS: Tuple[str, ...] = tuple(row[0] for row in FAL_OP_TABLE)  # the 9 ops the fal preset fills
IMAGE_OPS: Tuple[str, ...] = FAL_OPS + ("VECTOR_MANIPULATION",)  # every op of the image MCP server

# The 21 packs of c101 config/packs.json with their `requires` (core is always installed; requires are transitive).
PACK_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "core": (),
    "image": ("core",),
    "rust": ("core",),
    "cpp": ("core",),
    "go": ("core",),
    "haskell": ("core",),
    "julia": ("core",),
    "java": ("core",),
    "kotlin": ("core",),
    "computer-graphics": ("core",),
    "computer-science": ("core",),
    "quantum": ("core",),
    "mathematics": ("core",),
    "physics": ("core",),
    "graphic-design": ("core", "image"),
    "coding-core": ("core",),
    "python": ("core", "coding-core"),
    "typescript": ("core", "coding-core"),
    "devops": ("core", "coding-core"),
    "writing": ("core",),
    "containers": ("core", "coding-core", "devops"),
}
PACK_NAMES: Tuple[str, ...] = tuple(PACK_REQUIRES)
_PACKS_WITHOUT_HUB = ("core", "image", "devops")

# `bin/stack keys` lists these (c101 config/policy.json expected_keys, then optional_keys), names only.
STACK_EXPECTED_KEYS: Tuple[str, ...] = ("EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY")
STACK_OPTIONAL_KEYS: Tuple[str, ...] = tuple(
    "IMAGE_%s_%s" % (op, field) for op in FAL_OPS for field in ("URL", "KEY", "MODEL")
) + ("CONTEXT7_API_KEY", "FIRECRAWL_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY")


class Sandbox:
    """A temporary HOME, TMPDIR and bin directory plus an environment that points the overlay at them."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="c101l-test-")
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.tmpdir = self.base / "tmp"
        self.bindir = self.base / "bin"
        for d in (self.home, self.tmpdir, self.bindir):
            d.mkdir()
        self.stubs: Dict[str, ArgvStub] = {}  # argv-logging stubs made through argv_stub(), by command name
        # By default no Claude Desktop is "running", whatever the machine running the tests has open.
        self.fake_command("pgrep", "exit 1")

    def close(self) -> None:
        self._tmp.cleanup()

    def env(self, **extra: str) -> Dict[str, str]:
        env = {
            "HOME": str(self.home),
            "TMPDIR": str(self.tmpdir),
            "PATH": "%s:/usr/local/bin:/usr/bin:/bin" % self.bindir,
            "LANG": "C.UTF-8",
            "USER": "tester",
            "C101L_PY": sys.executable,
            "C101_LINUX_LOGIN_SHELL": "/bin/bash",
        }
        env.update(extra)
        return env

    def run(self, args: List[str], check: bool = False, **extra: str) -> subprocess.CompletedProcess:
        res = subprocess.run(args, capture_output=True, text=True, env=self.env(**extra), cwd=str(self.base), timeout=120)
        if check and res.returncode != 0:
            raise AssertionError("%s failed (%d):\n%s\n%s" % (args, res.returncode, res.stdout, res.stderr))
        return res

    def snapshot(self, path: Optional[Path] = None) -> Dict[str, str]:
        """Relative path -> 'dir' | 'link:<target>' | file content, for every entry under path (default HOME)."""
        base = path or self.home
        out: Dict[str, str] = {}
        for p in sorted(base.rglob("*")):
            rel = str(p.relative_to(base))
            if p.is_symlink():
                out[rel] = "link:" + os.readlink(p)
            elif p.is_dir():
                out[rel] = "dir"
            else:
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
        return out

    def fake_podman(self) -> Path:
        """A podman that answers --version and records any other call in $HOME/podman-was-called (it must never run
        in a dry run or in the doctor of a user who never used podman: `podman info` initialises storage)."""
        return self.fake_command("podman", 'case "$1" in --version) echo "podman version 5.0.0" ;; *) touch "$HOME/podman-was-called" ;; esac')

    def fake_command(self, name: str, body: str) -> Path:
        path = self.bindir / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    # --- parity fixtures: thin wrappers over the module-level helpers further down ---------------------------------

    @property
    def logdir(self) -> Path:
        """Where argv_stub logs go: next to HOME, TMPDIR and bin, so HOME snapshots and TMPDIR listings stay untouched."""
        return self.base / "stub-logs"

    @property
    def stack_env(self) -> Path:
        """The default stack.env location in this sandbox (CLAUDE_STACK_ENV is not set unless a test sets it)."""
        return self.home / ".claude" / "stack.env"

    def argv_stub(self, name: str, **kw: Any) -> ArgvStub:
        """Put an argv-logging stub for `name` first on PATH; see make_argv_stub for the keywords (code, stdout, stderr,
        rules, passthrough, env_names, env_values). timeout and sudo run the command they wrap; everything else just answers."""
        stub = make_argv_stub(self.bindir, name, log=self.logdir / (name + ".jsonl"), **kw)
        self.stubs[name] = stub
        return stub

    def argv_stubs(self, *names: str, **kw: Any) -> Dict[str, ArgvStub]:
        """argv_stub for several commands, e.g. sb.argv_stubs("uvx", "npx", "timeout"); keywords apply to all of them."""
        return {name: self.argv_stub(name, **kw) for name in names}

    def write_stack_env(self, content: Union[str, Mapping[str, str], None] = None, **kw: Any) -> Path:
        """write_stack_env(self.home, content, **kw): the stack.env fixture at $HOME/.claude/stack.env."""
        return write_stack_env(self.home, content, **kw)

    def link_c101(self, c101: Path) -> Path:
        """Make $HOME/.claude/stack point at a (stub) c101 checkout, as c101's installer does."""
        return link_c101(self.home, c101)

    def secret_leaks(self, *results: Any, trees: Optional[Iterable[Path]] = None, allow: Optional[Iterable[Any]] = None,
                     argv_logs: Optional[Iterable[Any]] = None, c101: Optional[Path] = None,
                     needles: Optional[Sequence[str]] = None, check_env: bool = False) -> List[str]:
        """Where SECRET_SENTINEL (or `needles`) shows up: in the stdout/stderr/argv of the given CompletedProcess results,
        in any file, file name or link target under `trees` (default HOME and TMPDIR) except `allow` (default
        STACK_ENV_ALLOW), and in the argv log of every stub made with argv_stub plus both stub logs of `c101`.
        An empty list means clean. See find_secret_leaks."""
        logs: List[Any] = list(self.stubs.values()) if argv_logs is None else list(argv_logs)
        if c101 is not None:
            logs += [stub_log_path(c101, "stack"), stub_log_path(c101, "image-router")]
        return find_secret_leaks(
            results=results,
            trees=[self.home, self.tmpdir] if trees is None else list(trees),
            argv_logs=logs,
            allow=STACK_ENV_ALLOW if allow is None else tuple(allow),
            needles=(SECRET_SENTINEL,) if needles is None else tuple(needles),
            check_env=check_env,
        )

    def assert_no_secret_leak(self, *results: Any, **kw: Any) -> None:
        """Fail, naming only locations, when secret_leaks finds anything (same keywords)."""
        leaks = self.secret_leaks(*results, **kw)
        if leaks:
            raise AssertionError("the secret sentinel leaked into: " + "; ".join(leaks))


def make_stub_c101(root: Path, with_venv: bool = True) -> Path:
    """A minimal fake c101 checkout: enough for source validation and for recording the flags it was run with.

    Besides the original files (install.sh logging to calls.log, stack/install.py, .claude/settings.json, .venv) it now
    holds the c101 files the key, preset, pre-warm, learning and doctor features read: config/stack.env.example,
    config/stack.env.fal.example, config/packs.json (21 packs), config/image-ops.json, bin/image-router (--check status
    on stderr, names only), bin/stack (logs every call; answers keys|depth|learn) and bin/neural-memory-mcp (version
    pin). Use build_stub_c101 for the variants (no config/, other neural-memory pin). None of it needs the network."""
    return build_stub_c101(root, with_venv=with_venv)


def bash_lib(sb: Sandbox, snippet: str, **extra: str) -> subprocess.CompletedProcess:
    """Run a snippet with the overlay libraries sourced (same environment contract as the entry points)."""
    script = (
        "set -Eeuo pipefail\n"
        "C101L_ROOT=%s\n" % ROOT
        + "C101L_TMP=$(mktemp -d \"$TMPDIR/lib.XXXXXX\")\n"
        ". \"$C101L_ROOT/lib/common.sh\"\n"
        ". \"$C101L_ROOT/lib/detect.sh\"\n"
        ". \"$C101L_ROOT/lib/profiles.sh\"\n"
        ". \"$C101L_ROOT/lib/deps.sh\"\n"
        ". \"$C101L_ROOT/lib/c101.sh\"\n"
        ". \"$C101L_ROOT/lib/desktop.sh\"\n"
        ". \"$C101L_ROOT/lib/pathprofile.sh\"\n"
        ". \"$C101L_ROOT/lib/services.sh\"\n"
        + snippet + "\n"
    )
    return sb.run(["bash", "-c", script], **extra)


def installer_args(*args: str) -> List[str]:
    """install-linux.sh argv; adds --allow-root when the tests themselves run as root (CI containers)."""
    extra = ["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
    return [str(ROOT / "install-linux.sh"), *extra, *args]


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# =================================================================================================================
# Parity fixtures. Nothing below runs a stub, the installer or any network call at import time.
# =================================================================================================================

# --- stack.env: parse, write, mode, backups -------------------------------------------------------------------


def parse_stack_env_text(text: str) -> Dict[str, str]:
    """Parse stack.env text exactly as c101 does (stack/envfile.py parse, mirrored by bin/mcp-run and bin/mcp-headers):
    KEY=VALUE lines, an optional `export `, matching single or double quotes, an unquoted value loses its ` # comment`
    and surrounding whitespace, and the last assignment of a name wins. The stub programs embed this very source."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.lstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        m = re.match(r'^"(.*)"$', val) or re.match(r"^'(.*)'$", val)
        if m:
            val = m.group(1)
        else:
            val = re.split(r"\s#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def read_stack_env(path: Union[str, Path], missing_ok: bool = False) -> Dict[str, str]:
    """The NAME -> value map of a stack.env file, followed through symlinks. A missing file raises unless missing_ok."""
    try:
        return parse_stack_env_text(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise


def write_stack_env(home: Path, content: Union[str, Mapping[str, str], None] = None, *, mode: int = 0o600,
                    target_dir: Optional[Path] = None, relative: bool = False, path: Optional[Path] = None) -> Path:
    """Write a stack.env fixture and return its location ($HOME/.claude/stack.env unless `path` is given).

    content: None writes the stub c101's config/stack.env.example text (what c101's base() copies into place); a str is
    written as is; a mapping becomes `NAME=value` lines (values are not quoted, so a test can plant odd ones).
    mode: the exact permission bits (default 0o600); pass 0o644 to build the world-readable case for the doctor.
    target_dir: the file is written there (same file name) and the location becomes a symlink to it, absolute unless
    `relative`; this is the dotfiles case. Read the target with `.resolve()`. An existing file or link at the location
    is replaced, never written through."""
    loc = Path(path) if path is not None else Path(home) / ".claude" / "stack.env"
    if content is None:
        text = stack_env_example_text()
    elif isinstance(content, str):
        text = content
    else:
        text = "".join("%s=%s\n" % (name, value) for name, value in content.items())
    loc.parent.mkdir(parents=True, exist_ok=True)
    if loc.is_symlink() or loc.exists():
        loc.unlink()
    if target_dir is None:
        loc.write_text(text, encoding="utf-8")
        loc.chmod(mode)
        return loc
    target = Path(target_dir) / loc.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.write_text(text, encoding="utf-8")
    target.chmod(mode)
    loc.symlink_to(os.path.relpath(target, loc.parent) if relative else str(target))
    return loc


def file_mode(path: Union[str, Path], follow: bool = True) -> int:
    """The permission bits of a file (os.stat, so a symlink reports its target; follow=False reports the link itself)."""
    st = os.stat(path) if follow else os.lstat(path)
    return stat.S_IMODE(st.st_mode)


def backups_of(path: Union[str, Path]) -> List[Path]:
    """The `<name>.bak-*` copies next to a file and, for a symlink, next to its target (where lib/stackenv.py puts them)."""
    p = Path(path)
    dirs = {p.parent}
    names = {p.name}
    real = p.resolve()
    dirs.add(real.parent)
    names.add(real.name)
    return sorted({q for d in dirs for n in names for q in d.glob(n + ".bak-*")})


def env_file_allowances(path: Union[str, Path]) -> Tuple[Path, str]:
    """`allow` entries for find_secret_leaks / scan_tree that cover a stack.env wherever it lives (including a symlink
    target outside HOME): the file itself and its backups."""
    p = Path(path).absolute()
    return (p, str(p.parent / (p.name + ".bak-*")))


def fal_preset_values(key: str = "") -> Dict[str, str]:
    """The IMAGE_* lines (plus VECTOR_MANIPULATION_MODEL and FAL_KEY) a correct fal-preset merge leaves in stack.env when
    the single fal key is `key` ("" = the key-less merge: URL, MODEL and PARAMS present, every KEY empty)."""
    vals: Dict[str, str] = {}
    for op, model, params in FAL_OP_TABLE:
        vals["IMAGE_%s_URL" % op] = FAL_URL
        vals["IMAGE_%s_KEY" % op] = key
        vals["IMAGE_%s_MODEL" % op] = model
        if params:
            vals["IMAGE_%s_PARAMS" % op] = params
    vals["IMAGE_VECTOR_MANIPULATION_MODEL"] = "claude"
    vals["FAL_KEY"] = key
    return vals


def sentinel_for(name: str) -> str:
    """A per-key variant of SECRET_SENTINEL (it contains the sentinel, so one scan for SECRET_SENTINEL finds them all)."""
    return "%s-%s" % (SECRET_SENTINEL, name)


# --- the fake c101 config files --------------------------------------------------------------------------------

_RESEARCH_NOTES = (
    ("EXA_API_KEY", "https://dashboard.exa.ai/api-keys   (exa: works anonymously with rate limits)"),
    ("JINA_API_KEY", "https://jina.ai/api-dashboard       (jina: read_url works without; search needs it)"),
    ("SPIDER_API_KEY", "https://spider.cloud/api-keys       (spider: required)"),
    ("CONTEXT7_API_KEY", "https://context7.com/dashboard      (optional; higher rate limits)"),
)


def _noted(left: str, note: str, width: int = 31) -> str:
    """`left` padded to `width` columns and followed by `# note` (just `left` when there is no note)."""
    return ("%-*s# %s" % (width, left, note)) if note else left


def stack_env_example_text() -> str:
    """The stub config/stack.env.example: the real file's layout (empty values with trailing `# comments`, one block per
    image op, a commented-out fal example, optional fallbacks, desktop-sandbox lines) with fake model ids and no values."""
    lines = [
        "# ~/.claude/stack.env - API keys for the agent stack. Copy this file there and",
        "# `chmod 600 ~/.claude/stack.env`. It is PARSED (KEY=VALUE lines only), never",
        "# sourced, by bin/mcp-run and bin/mcp-headers. Leave a value empty to disable",
        "# that provider; routers skip providers without keys.",
        "#",
        "# Check what is set (names only):  bin/stack keys",
        "#",
        "# Running every image operation on fal? Start from config/stack.env.fal.example instead.",
        "# (STUB for the c101-linux tests: same layout as c101's file, fake model ids, no values.)",
        "",
        "# --- web research / crawling (remote MCP servers via headersHelper) ---------",
    ]
    for name, note in _RESEARCH_NOTES:
        lines.append(_noted(name + "=", note, 24))
    lines += [
        "",
        "# --- images: one endpoint per operation (BYOK; no fallbacks) ---------------",
        "# Each operation of the `image` MCP server uses exactly the endpoint, key and model tag set",
        "# here, and its tool is EXPOSED ONLY when all three are set (restart/reconnect the server via",
        "# /mcp after editing). The protocol is detected from the URL host; force it with",
        "# IMAGE_<OP>_API=openrouter|opper|openai|fal (TRACE and UPSCALE also accept IMAGE_<OP>_API=local).",
        "# Optional IMAGE_<OP>_PARAMS='{\"quality\":\"high\"}' adds fixed request parameters.",
    ]
    for op, purpose, tool, example in _OP_LAYOUT:
        lines.append(_noted("IMAGE_%s_URL=" % op, "%s -> %s" % (purpose, tool)))
        lines.append("IMAGE_%s_KEY=" % op)
        lines.append(_noted("IMAGE_%s_MODEL=" % op, example))
    lines += [
        "IMAGE_VECTOR_MANIPULATION_MODEL=claude   # 'claude' (default): agents edit SVG themselves; svg_transform hidden",
        "IMAGE_VECTOR_MANIPULATION_URL=           # set URL/KEY/MODEL to expose svg_transform with an external model",
        "IMAGE_VECTOR_MANIPULATION_KEY=",
        "",
        "# --- example: route operations to fal (uncomment the ops you want) -----------",
        "# fal speaks its queue API: URL is always %s, KEY is your fal key," % FAL_URL,
        "# MODEL is the fal endpoint id exactly as shown on the fal model pages. One fal key serves every",
        "# operation: repeat it in each IMAGE_<OP>_KEY you point at fal. An uncommented line here overrides",
        "# the empty one above (the last assignment wins).",
        "#",
    ]
    for op, model, _params in FAL_OP_TABLE:
        if op not in ("GEN", "EDIT", "SEGMENT", "BG_REMOVAL", "UPSCALE", "VECTOR", "TRACE"):
            continue
        lines.append("# IMAGE_%s_URL=%s" % (op, FAL_URL))
        lines.append("# IMAGE_%s_KEY=%s" % (op, FAL_PLACEHOLDER))
        if op == "EDIT":
            lines.append("# IMAGE_EDIT_MODEL=%s       # masked inpaint instead: fakeco/image-gen-1.0/fill" % model)
        else:
            lines.append("# IMAGE_%s_MODEL=%s" % (op, model))
        if op == "UPSCALE":
            lines.append("# optional fixed request parameters (keep a quoted value alone on its line, no trailing comment):")
            lines.append("# IMAGE_UPSCALE_PARAMS='{\"upscale_factor\":2}'")
        lines.append("#")
    lines += [
        "# FAL_KEY=%s        # only for fal's hosted MCP server (bin/stack mcp enable fal)" % FAL_PLACEHOLDER,
        "",
        "# --- optional fallbacks (see config/mcp.optional.json) -----------------------",
        "FIRECRAWL_API_KEY=",
        "TAVILY_API_KEY=",
        "BRAVE_API_KEY=",
        "RUNWARE_API_KEY=",
        "",
        "# --- sandboxed computer use (desktop-sandbox MCP server; all optional) ------",
        "# This server receives ONLY DESKTOP_SANDBOX_* lines (bin/mcp-run --only), never your keys.",
        "# DESKTOP_SANDBOX_RUNTIME=docker          # docker | podman | absolute path (default: first found)",
        "# DESKTOP_SANDBOX_NETWORK=none            # none (default) | bridge = the sandbox may reach the internet",
        "# DESKTOP_SANDBOX_CPUS=2",
        "",
    ]
    return "\n".join(lines)


def fal_example_text() -> str:
    """The stub config/stack.env.fal.example: every image op on fal, `your-fal-key` as the placeholder in each
    IMAGE_<OP>_KEY, URL https://queue.fal.run, fake endpoint ids, quoted JSON PARAMS lines alone on their line,
    VECTOR_MANIPULATION_MODEL=claude, an empty FAL_KEY at the end (hosted fal MCP) and empty research keys."""
    lines = [
        "# ~/.claude/stack.env for the case where EVERY image operation runs on fal (https://fal.ai).",
        "# (STUB for the c101-linux tests: same layout as c101's file, fake endpoint ids.)",
        "#",
        "#   cp config/stack.env.fal.example ~/.claude/stack.env && chmod 600 ~/.claude/stack.env",
        "#   then replace every `%s` with your key and run: bin/stack keys" % FAL_PLACEHOLDER,
        "#",
        "# The file is PARSED (KEY=VALUE lines only), never sourced, so there is no variable expansion: the router has",
        "# no shared-key fallback and reads each operation's own IMAGE_<OP>_KEY, so the same fal key is repeated on",
        "# every operation. Keep comments on their own lines.",
        "",
        "# --- web research / crawling (remote MCP servers via headersHelper; not fal) ------------------------------",
        "EXA_API_KEY=",
        "JINA_API_KEY=",
        "SPIDER_API_KEY=",
        "CONTEXT7_API_KEY=",
    ]
    layout = {op: (purpose, tool) for op, purpose, tool, _example in _OP_LAYOUT}
    for op, model, params in FAL_OP_TABLE:
        purpose, tool = layout[op]
        lines.append("")
        lines.append("# --- %s -> %s ----------------------------------------------------------------" % (purpose, tool))
        lines.append("IMAGE_%s_URL=%s" % (op, FAL_URL))
        lines.append("IMAGE_%s_KEY=%s" % (op, FAL_PLACEHOLDER))
        lines.append("IMAGE_%s_MODEL=%s" % (op, model))
        if params:
            lines.append("IMAGE_%s_PARAMS='%s'" % (op, params))
    lines += [
        "",
        "# --- SVG manipulation -------------------------------------------------------------------------------------",
        "# fal hosts image models, not the chat model svg_transform needs, so agents edit SVG text themselves",
        "IMAGE_VECTOR_MANIPULATION_MODEL=claude",
        "",
        "# --- fal's own hosted MCP server (optional; ~60 tools: model search, run_model, jobs) --------------------",
        "# only used after `bin/stack mcp enable fal`; the image server above does not need it",
        "FAL_KEY=",
        "",
    ]
    return "\n".join(lines)


def packs_json_text() -> str:
    """The stub config/packs.json: the 21 real pack names with their real `requires` (skills and agents are fake)."""
    packs: Dict[str, Any] = {}
    for name, requires in PACK_REQUIRES.items():
        entry: Dict[str, Any] = {"description": "stub pack %s" % name, "requires": list(requires)}
        skills: List[str] = []
        if name not in _PACKS_WITHOUT_HUB:
            entry["hub"] = "%s-craft" % name
            skills.append("%s-craft" % name)
        entry["skills"] = skills
        entry["agents"] = []
        packs[name] = entry
    doc = {
        "_doc": "STUB of c101 config/packs.json for the c101-linux tests: real pack names and requires, fake contents.",
        "granular_listing": "name-only",
        "packs": packs,
    }
    return json.dumps(doc, indent=2) + "\n"


def image_ops_json_text() -> str:
    """The stub config/image-ops.json: the ten ops with their MCP tool and the five IMAGE_<OP>_* variable names."""
    ops: Dict[str, Any] = {}
    rows = list(_OP_LAYOUT) + [("VECTOR_MANIPULATION", "SVG edits by an external model", "svg_transform", "")]
    for op, purpose, tool, _example in rows:
        ops[op] = {
            "tools": [tool],
            "purpose": purpose,
            "env": ["IMAGE_%s_%s" % (op, field) for field in ("URL", "KEY", "MODEL", "API", "PARAMS")],
        }
    doc = {
        "_doc": "STUB of c101 config/image-ops.json for the c101-linux tests: documentation only, no secrets.",
        "version": 2,
        "env_file": "~/.claude/stack.env (override the path with CLAUDE_STACK_ENV); template: config/stack.env.example",
        "env_pattern": {field.lower(): "IMAGE_<OP>_%s" % field for field in ("URL", "KEY", "MODEL", "API", "PARAMS")},
        "ops": ops,
        "tool_list": "An operation's tool is listed only while the operation is configured.",
    }
    return json.dumps(doc, indent=2) + "\n"


def neural_memory_mcp_text(version: str = NM_VERSION) -> str:
    """The stub bin/neural-memory-mcp. Like c101's script the pin is a `NM_VERSION="x.y.z"` line used as
    `--from "neural-memory==${NM_VERSION}"`; there is no literal `neural-memory==x.y.z` anywhere, so read it with
    parse_nm_pin (the overlay's pre-warm must do the same). The script refuses to run: it is only a pin source."""
    return (
        "#!/usr/bin/env bash\n"
        "# STUB of c101 bin/neural-memory-mcp: only the version pin matters to the tests.\n"
        "set -euo pipefail\n"
        "\n"
        'NM_VERSION="%s"\n'
        'export NEURALMEMORY_DIR="${NEURALMEMORY_DIR:-${CLAUDE_STACK_DATA:-${HOME:-/nonexistent}/.claude/stack-data}/neural-memory}"\n'
        'nm=(uvx --quiet --python 3.13 --from "neural-memory==${NM_VERSION}")\n'
        "\n"
        'echo "stub neural-memory-mcp: not a runnable server" >&2\n'
        "exit 64\n"
        'exec "${nm[@]}" nmem-mcp\n' % version
    )


def parse_nm_pin(c101: Union[str, Path]) -> str:
    """The version in a (stub) c101's bin/neural-memory-mcp, read from its NM_VERSION="..." line."""
    text = (Path(c101) / "bin" / "neural-memory-mcp").read_text(encoding="utf-8")
    m = re.search(r'^NM_VERSION="([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("no NM_VERSION line in %s/bin/neural-memory-mcp" % c101)
    return m.group(1)


# --- stub programs: one Python source template shared by bin/stack, bin/image-router and the argv stubs ---------

_STUB_PRELUDE = r'''from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys

CONFIG = json.loads(__CONFIG__)


def _stdin_kind():
    try:
        st = os.fstat(0)
    except OSError:
        return "closed"
    try:
        if os.isatty(0):
            return "tty"
        if os.path.samestat(st, os.stat("/dev/null")):
            return "devnull"
    except OSError:
        pass
    if stat.S_ISFIFO(st.st_mode):
        return "pipe"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "other"


def _record(argv):
    """Append one JSON line: argv, cwd, what stdin was, PATH, which watched variables are set, the values of the few
    non-secret variables the config lists (env_values) and the NAMES (never the values) of variables whose value holds
    the secret sentinel. A log that cannot be written is ignored."""
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    marker = CONFIG["sentinel"]
    rec = {
        "stub": CONFIG["name"],
        "argv": argv,
        "cwd": cwd,
        "stdin": _stdin_kind(),
        "path": os.environ.get("PATH", ""),
        "env_present": dict((n, n in os.environ) for n in CONFIG["env_names"]),
        "env_values": dict((n, "<sentinel>" if marker in os.environ[n] else os.environ[n])
                           for n in CONFIG.get("env_values", []) if n in os.environ),
        "env_with_sentinel": sorted(k for k, v in os.environ.items() if marker in v),
    }
    line = (json.dumps(rec, sort_keys=True) + "\n").encode("ascii")
    try:
        os.makedirs(os.path.dirname(CONFIG["log"]), exist_ok=True)
        fd = os.open(CONFIG["log"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass
'''

_ARGV_STUB_MAIN = r'''
_OPTS_WITH_VALUE = {
    "timeout": ("-k", "--kill-after", "-s", "--signal"),
    "sudo": ("-u", "--user", "-g", "--group", "-C", "-D", "-h", "--host", "-p", "--prompt", "-R", "-r", "-t", "-T", "-U"),
}


def _skip_options(argv, with_value):
    i = 0
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "-":
        opt = argv[i]
        i += 1
        if opt == "--":
            break
        if opt in with_value:
            i += 1
    return argv[i:]


def _wrapped_command(kind, argv):
    rest = _skip_options(argv, _OPTS_WITH_VALUE.get(kind, ()))
    if kind == "timeout":
        return rest[1:]  # the first word left is DURATION
    return rest


def _may_run(cmd0):
    """Passthrough never starts the real uvx, npx, podman, systemctl, ...: only a stub that sits in this stub's bin dir."""
    if os.path.basename(cmd0) not in CONFIG["deny_real"]:
        return True
    found = shutil.which(cmd0)
    if found is None:
        return True
    return os.path.realpath(os.path.dirname(found)) == os.path.realpath(CONFIG["bindir"])


def _respond(spec):
    out = spec.get("stdout", "")
    err = spec.get("stderr", "")
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    sys.exit(int(spec.get("exit", 0)))


def main():
    argv = sys.argv[1:]
    _record(argv)
    for rule in CONFIG["rules"]:
        if all(tok in argv for tok in rule["args"]):
            _respond(rule)
    kind = CONFIG["passthrough"]
    if kind:
        cmd = _wrapped_command(kind, argv)
        if not cmd:
            sys.exit(0)
        if not _may_run(cmd[0]):
            sys.stderr.write("%s stub: refusing to run the real %s (make a stub for it first)\n" % (CONFIG["name"], cmd[0]))
            sys.exit(126)
        try:
            os.execvp(cmd[0], cmd)
        except OSError as exc:
            sys.stderr.write("%s stub: cannot run %s: %s\n" % (CONFIG["name"], cmd[0], exc))
            sys.exit(127)
    _respond(CONFIG["default"])


if __name__ == "__main__":
    main()
'''

_STACK_STUB_MAIN = r'''
# Stub of c101 bin/stack. Every call is logged; keys|depth|learn get canned answers shaped like the real ones
# (names only, never a value); anything else prints `stub stack <args>` as the original stub did.
# stack-stub.json next to the log can override stdout, stderr and exit per "sub" or "sub action".


def _env_file():
    return os.environ.get("CLAUDE_STACK_ENV") or os.path.join(os.path.expanduser("~"), ".claude", "stack.env")


def _data_dir():
    return os.environ.get("CLAUDE_STACK_DATA") or os.path.join(os.path.expanduser("~"), ".claude", "stack-data")


def _read_env():
    try:
        with open(_env_file(), encoding="utf-8") as fh:
            return True, parse_stack_env_text(fh.read())
    except OSError:
        return False, {}


def _keys_text():
    present, values = _read_env()
    lines = ["env file: %s (%s)" % (_env_file(), "present" if present else "MISSING")]
    for name in CONFIG["expected_keys"]:
        lines.append("  %-28s %s" % (name, "set" if values.get(name) else "missing"))
    if CONFIG["optional_keys"]:
        lines.append("optional:")
        for name in CONFIG["optional_keys"]:
            lines.append("  %-28s %s" % (name, "set" if values.get(name) else "missing"))
    return "\n".join(lines) + "\n"


def _defaults(argv):
    sub = argv[0] if argv else ""
    action = argv[1] if len(argv) > 1 else ""
    if sub == "keys":
        return _keys_text(), "", 0
    if sub == "depth":
        return "", "", 0
    if sub == "learn":
        if action == "ingest":
            return "ingested 0 agent runs\n", "", 0
        if action == "update":
            return "updated posteriors for 0 agent types\n", "", 0
        if action == "apply":
            return "no changes\n", "", 0
        if action == "run":
            return "{}\n", "", 0
        if action == "history":
            return "no history yet (%s)\n" % os.path.join(_data_dir(), "history.jsonl"), "", 0
        if action == "report":
            if not os.path.exists(os.path.join(_data_dir(), "recommendations.json")):
                return "no recommendations yet; run `stack learn ingest --all && stack learn update`\n", "", 1
            return "agent  n  gamma  maxTurns  effort  P(fail)  tokens out [min..max]\n", "", 0
        return "", "", 0
    return "stub stack" + "".join(" " + a for a in argv) + "\n", "", 0


def _override(table, argv):
    for key in (" ".join(argv[:2]), argv[0] if argv else ""):
        if key in table:
            return table[key]
    return None


def main():
    argv = sys.argv[1:]
    _record(argv)
    try:
        with open(CONFIG["overrides"], encoding="utf-8") as fh:
            ov = json.load(fh)
    except (OSError, ValueError):
        ov = {}
    out, err, code = _defaults(argv)
    o = _override(ov.get("stdout", {}), argv)
    e = _override(ov.get("stderr", {}), argv)
    c = _override(ov.get("exit", {}), argv)
    if o is not None:
        out = o
    if e is not None:
        err = e
    if c is not None:
        code = int(c)
    sys.stdout.write(out)
    sys.stdout.flush()
    sys.stderr.write(err)
    sys.stderr.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
'''

_IMAGE_ROUTER_STUB_MAIN = r'''
# Stub of c101 bin/image-router. `--check` prints the per-operation status to stderr in the real format and never a
# key: `NOT CONFIGURED (missing IMAGE_<OP>_URL, ...)` or `<api> <host> <model>` for a configured op. Any other call only
# logs and exits 0 (the real one would start the MCP server).


def _env_file():
    return os.environ.get("CLAUDE_STACK_ENV") or os.path.join(os.path.expanduser("~"), ".claude", "stack.env")


def _api_for(host, forced):
    if forced:
        return forced
    if host.endswith("fal.run") or host.endswith("fal.ai"):
        return "fal"
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if host.endswith("opper.ai"):
        return "opper"
    return "openai"


def _status_line(op, values):
    def get(field):
        return values.get("IMAGE_%s_%s" % (op, field), "")

    url, key, model, api = get("URL"), get("KEY"), get("MODEL"), get("API").strip().lower()
    if op == "VECTOR_MANIPULATION" and (model or "claude").strip().lower() == "claude":
        return "  %-20s claude (agent edits SVG; svg_transform hidden)" % op
    if api == "local" and op in ("TRACE", "UPSCALE"):
        return "  %-20s local" % op
    missing = ["IMAGE_%s_%s" % (op, f) for f, v in (("URL", url), ("KEY", key), ("MODEL", model)) if not v]
    if missing:
        return "  %-20s NOT CONFIGURED (missing %s)" % (op, ", ".join(missing))
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", url).split("/")[0].split(":")[0]
    return ("  %-20s %s %s %s" % (op, _api_for(host, api), host, model)).rstrip()


def main():
    argv = sys.argv[1:]
    _record(argv)
    if "--check" in argv:
        path = _env_file()
        found = os.path.isfile(path)
        try:
            with open(path, encoding="utf-8") as fh:
                values = parse_stack_env_text(fh.read())
        except OSError:
            values = {}
        lines = ["env file: %s (%s)" % (path, "found" if found else "missing")]
        for op in CONFIG["ops"]:
            lines.append(_status_line(op, values))
        sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
'''


def _stub_source(main_src: str, config: Mapping[str, Any]) -> str:
    """Prelude with the config baked in, the stack.env parser (this module's own source) and a program body."""
    prelude = _STUB_PRELUDE.replace("__CONFIG__", repr(json.dumps(config, sort_keys=True)))
    return prelude + "\n\n" + inspect.getsource(parse_stack_env_text) + "\n" + main_src


def _write_py_stub(path: Path, source: str) -> Path:
    """An executable Python program: a `#!<this interpreter> -I` script, or (interpreter path too long or with spaces for
    a shebang) a /bin/sh launcher next to a hidden .py file."""
    py = sys.executable
    if len(py) <= 100 and not re.search(r"\s", py):
        path.write_text("#!%s -I\n%s" % (py, source), encoding="utf-8")
    else:
        script = path.with_name(".%s.stub.py" % path.name)
        script.write_text(source, encoding="utf-8")
        path.write_text("#!/bin/sh\nexec %s -I %s \"$@\"\n" % (shlex.quote(py), shlex.quote(str(script))), encoding="utf-8")
    path.chmod(0o755)
    return path


def read_jsonl(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """The records of a JSON-lines log; a missing log is an empty list."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- argv-logging stubs (uvx, npx, systemctl, podman, timeout, sudo, ...) ---------------------------------------


def rule(*args: str, stdout: str = "", stderr: str = "", code: int = 0) -> Dict[str, Any]:
    """An answer for calls whose argv contains every word in `args` (any order, extra words allowed; no words = every
    call). The first matching rule wins and is checked before passthrough and the default answer.
    Example: rule("is-active", "c101-learn-backstop.timer", stdout="active\\n") or rule("--version", stdout="uvx 0.5.0\\n")."""
    return {"args": list(args), "stdout": stdout, "stderr": stderr, "exit": code}


_DEFAULT_RULES: Dict[str, List[Dict[str, Any]]] = {
    "podman": [rule("--version", stdout="podman version 5.0.0\n")],
    "uv": [rule("--version", stdout="uv 0.5.0\n")],
    "uvx": [rule("--version", stdout="uvx 0.5.0\n")],
    "npx": [rule("--version", stdout="10.0.0\n")],
}
_DENY_REAL = ("uv", "uvx", "npx", "npm", "node", "podman", "docker", "systemctl", "claude", "sudo")


class ArgvStub:
    """Handle on one argv-logging stub made by make_argv_stub / Sandbox.argv_stub. Every call of the stub appends a JSON
    record: argv (list), cwd, stdin ("devnull" | "tty" | "pipe" | "file" | "closed" | "other"), path, env_present
    ({watched name: bool}), env_values ({name: value} for the non-secret names asked for) and env_with_sentinel (NAMES
    of variables whose value holds SECRET_SENTINEL)."""

    PROBES = (("--version",), ("-V",), ("version",))

    def __init__(self, name: str, path: Path, log: Path) -> None:
        self.name = name
        self.path = path
        self.log = log

    def calls(self, probes: bool = True) -> List[Dict[str, Any]]:
        """All recorded calls in order; probes=False drops bare `--version` / `-V` / `version` checks."""
        records = read_jsonl(self.log)
        if not probes:
            records = [r for r in records if tuple(r.get("argv", [])) not in self.PROBES]
        return records

    def argvs(self, probes: bool = True) -> List[List[str]]:
        """Just the argument lists (without the program name), e.g. [["--python", "3.13", "mcp-server-fetch", "--help"]]."""
        return [list(r["argv"]) for r in self.calls(probes)]

    def called(self, probes: bool = True) -> bool:
        return bool(self.calls(probes))

    def clear(self) -> None:
        """Forget the recorded calls."""
        if self.log.exists():
            self.log.unlink()

    def __repr__(self) -> str:
        return "ArgvStub(%s, log=%s)" % (self.name, self.log)


def make_argv_stub(bindir: Path, name: str, *, log: Optional[Path] = None, code: int = 0, stdout: str = "",
                   stderr: str = "", rules: Optional[Sequence[Mapping[str, Any]]] = None, passthrough: Optional[str] = "auto",
                   env_names: Sequence[str] = (), env_values: Sequence[str] = (),
                   deny_real: Sequence[str] = _DENY_REAL) -> ArgvStub:
    """An executable `bindir/name` that appends its argv (JSON lines, see ArgvStub) to `log` (default
    bindir/../stub-logs/<name>.jsonl) and then answers.

    Answer: the first matching `rules` entry (see rule(); None = a built-in `--version` answer for podman, uv, uvx and
    npx, [] = none), else passthrough, else code/stdout/stderr.
    passthrough: "auto" (timeout and sudo run the command they wrap, everything else just answers), "timeout" or "sudo"
    (force it for another name), None (never). The wrapped command is found on the stub's own PATH, so a stub of uvx or
    npx in the same bindir receives it. A passthrough never starts the real uv, uvx, npx, npm, node, podman, docker,
    systemctl, claude or sudo (`deny_real`): it exits 126 instead, so a forgotten stub cannot reach the network
    or the machine.
    env_names: variables whose presence (not value) each record notes.
    env_values: NON-secret variables (CLAUDE_STACK_MAIN_AGENT, PLAYWRIGHT_MCP_BROWSER, ...) whose value each record keeps;
    never list a key here. Logs are written even when stdin is /dev/null."""
    bindir = Path(bindir)
    bindir.mkdir(parents=True, exist_ok=True)
    log_path = Path(log) if log is not None else bindir.parent / "stub-logs" / (name + ".jsonl")
    if passthrough == "auto":
        passthrough = name if name in ("timeout", "sudo") else None
    config = {
        "name": name,
        "log": str(log_path),
        "bindir": str(bindir),
        "sentinel": SECRET_SENTINEL,
        "env_names": list(env_names),
        "env_values": list(env_values),
        "passthrough": passthrough,
        "rules": list(_DEFAULT_RULES.get(name, []) if rules is None else rules),
        "default": {"stdout": stdout, "stderr": stderr, "exit": code},
        "deny_real": list(deny_real),
    }
    path = _write_py_stub(bindir / name, _stub_source(_ARGV_STUB_MAIN, config))
    return ArgvStub(name, path, log_path)


# --- the stub c101 checkout --------------------------------------------------------------------------------------


def build_stub_c101(root: Path, with_venv: bool = True, *, with_config: bool = True, with_fal_example: bool = True,
                    nm_version: str = NM_VERSION) -> Path:
    """make_stub_c101 with options: with_config=False leaves out config/ entirely (the error paths of key provisioning),
    with_fal_example=False leaves out only config/stack.env.fal.example, nm_version sets the neural-memory pin.

    Files (see make_stub_c101 for what each is for): .python-version, stack/__init__.py, stack/install.py, install.sh
    (logs its flags to calls.log), .claude/settings.json, bin/stack, bin/image-router, bin/neural-memory-mcp, optional
    config/{stack.env.example, stack.env.fal.example, packs.json, image-ops.json} and, with_venv, .venv/bin/python (a
    link to the test interpreter: it has no PIL or vtracer, so an image-deps check cannot pass against the stub)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".python-version").write_text("3.13\n", encoding="utf-8")
    (root / "stack").mkdir(exist_ok=True)
    (root / "stack" / "__init__.py").write_text("", encoding="utf-8")
    (root / "stack" / "install.py").write_text(
        "import sys\nprint('STUB stack.install ' + ' '.join(sys.argv[1:]))\n", encoding="utf-8")
    install = root / "install.sh"
    install.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/calls.log\"\necho \"STUB install.sh $*\"\n",
        encoding="utf-8")
    install.chmod(0o755)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text('{"env": {"MAX_MCP_OUTPUT_TOKENS": "40000"}}\n', encoding="utf-8")
    (root / "bin").mkdir(exist_ok=True)
    stack_config = {
        "name": "stack",
        "log": str(root / STUB_LOG_NAMES["stack"]),
        "overrides": str(root / "stack-stub.json"),
        "sentinel": SECRET_SENTINEL,
        "env_names": [],
        "env_values": ["CLAUDE_STACK_MAIN_AGENT", "CLAUDE_STACK_ENV", "CLAUDE_STACK_DATA", "CLAUDE_PROJECTS_ROOT"],
        "expected_keys": list(STACK_EXPECTED_KEYS),
        "optional_keys": list(STACK_OPTIONAL_KEYS),
    }
    _write_py_stub(root / "bin" / "stack", _stub_source(_STACK_STUB_MAIN, stack_config))
    router_config = {
        "name": "image-router",
        "log": str(root / STUB_LOG_NAMES["image-router"]),
        "sentinel": SECRET_SENTINEL,
        "env_names": [],
        "env_values": ["CLAUDE_STACK_ENV"],
        "ops": list(IMAGE_OPS),
    }
    _write_py_stub(root / "bin" / "image-router", _stub_source(_IMAGE_ROUTER_STUB_MAIN, router_config))
    nm = root / "bin" / "neural-memory-mcp"
    nm.write_text(neural_memory_mcp_text(nm_version), encoding="utf-8")
    nm.chmod(0o755)
    if with_config:
        cfg = root / "config"
        cfg.mkdir(exist_ok=True)
        (cfg / "stack.env.example").write_text(stack_env_example_text(), encoding="utf-8")
        if with_fal_example:
            (cfg / "stack.env.fal.example").write_text(fal_example_text(), encoding="utf-8")
        (cfg / "packs.json").write_text(packs_json_text(), encoding="utf-8")
        (cfg / "image-ops.json").write_text(image_ops_json_text(), encoding="utf-8")
    if with_venv:
        (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        link = root / ".venv" / "bin" / "python"
        if not link.exists():
            link.symlink_to(sys.executable)
    return root


def stub_log_path(c101: Union[str, Path], prog: str = "stack") -> Path:
    """The JSON-lines call log of a stub c101 program: "stack" (bin/stack) or "image-router" (bin/image-router)."""
    return Path(c101) / STUB_LOG_NAMES[prog]


def stub_calls(c101: Union[str, Path], prog: str = "stack") -> List[Dict[str, Any]]:
    """Every recorded call of the stub bin/stack (or bin/image-router) as the records ArgvStub.calls returns."""
    return read_jsonl(stub_log_path(c101, prog))


def stack_calls(c101: Union[str, Path], *subcommands: str) -> List[List[str]]:
    """argv (without the program) of every logged bin/stack call; with `subcommands`, only calls whose first word is one
    of them, e.g. stack_calls(stub, "learn") -> [["learn", "run", "--transcript", "/x/s1.jsonl"]]."""
    argvs = [list(r["argv"]) for r in stub_calls(c101, "stack")]
    if subcommands:
        argvs = [a for a in argvs if a and a[0] in subcommands]
    return argvs


def configure_stack_stub(c101: Union[str, Path], stdout: Optional[Mapping[str, str]] = None,
                         stderr: Optional[Mapping[str, str]] = None, exit_codes: Optional[Mapping[str, int]] = None) -> Path:
    """Override what the stub bin/stack answers. Keys are a subcommand ("doctor", "keys") or "sub action" ("learn run"),
    the more specific one wins. Example: configure_stack_stub(stub, exit_codes={"learn run": 1, "doctor": 1})."""
    path = Path(c101) / "stack-stub.json"
    data: Dict[str, Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for field, table in (("stdout", stdout), ("stderr", stderr), ("exit", exit_codes)):
        if table:
            data.setdefault(field, {}).update(table)
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    return path


def link_c101(home: Path, c101: Path) -> Path:
    """$HOME/.claude/stack -> the (stub) c101 checkout, the link c101's installer makes and the doctor reads."""
    link = Path(home) / ".claude" / "stack"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(Path(c101).resolve())
    return link


# --- learning backstop fixtures (layout from the plan: ${CLAUDE_PROJECTS_ROOT:-~/.claude/projects}/*/*.jsonl and
# ${CLAUDE_STACK_DATA:-~/.claude/stack-data}/obs/<stem>.json) ---------------------------------------------------


def _age(path: Path, age_minutes: float) -> Path:
    stamp = time.time() - age_minutes * 60.0
    os.utime(path, (stamp, stamp))
    return path


def make_transcript(home: Path, stem: str, *, project: str = "proj", age_minutes: float = 120.0,
                    text: str = '{"type":"summary"}\n', projects_root: Optional[Path] = None) -> Path:
    """A session transcript `<projects root>/<project>/<stem>.jsonl` whose mtime is `age_minutes` in the past."""
    base = Path(projects_root) if projects_root is not None else Path(home) / ".claude" / "projects"
    path = base / project / (stem + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _age(path, age_minutes)


def make_obs(home: Path, stem: str, *, age_minutes: float = 0.0, data_dir: Optional[Path] = None) -> Path:
    """An ingestion marker `<stack-data>/obs/<stem>.json` whose mtime is `age_minutes` in the past (a transcript older
    than its obs file counts as already ingested)."""
    base = Path(data_dir) if data_dir is not None else Path(home) / ".claude" / "stack-data"
    path = base / "obs" / (stem + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return _age(path, age_minutes)


# --- leak scanning ----------------------------------------------------------------------------------------------


def _as_text(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    if isinstance(data, (list, tuple)):
        return "\0".join(_as_text(item) for item in data)
    return str(data)


def contains_secret(data: Any, needles: Sequence[str] = (SECRET_SENTINEL,)) -> bool:
    """True when any needle occurs in a str, bytes or list of them (an argv)."""
    text = _as_text(data)
    return any(needle in text for needle in needles)


def _split_allow(allow: Iterable[Union[str, Path]]) -> Tuple[set, List[str]]:
    exact = set()
    patterns: List[str] = []
    for item in allow:
        if isinstance(item, Path):
            exact.add(str(item.absolute()))
            try:
                exact.add(str(item.resolve()))
            except OSError:
                pass
        else:
            patterns.append(str(item))
    return exact, patterns


def _is_allowed(rel: str, path: Path, exact: set, patterns: Sequence[str]) -> bool:
    if any(fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(str(path), pat) for pat in patterns):
        return True
    return bool(exact) and (str(path) in exact or str(path.resolve()) in exact)


def scan_tree(root: Union[str, Path], needles: Sequence[str] = (SECRET_SENTINEL,),
              allow: Iterable[Union[str, Path]] = ()) -> List[str]:
    """Locations under `root` that hold a needle, as `file:<rel>`, `name:<rel>` (in a file or directory name) or
    `link:<rel>` (in a symlink target). Symlinks are not followed: scan their targets' tree on its own. `allow`: Path
    entries are exact files; str entries are fnmatch patterns on the path relative to `root` or the absolute path
    (STACK_ENV_ALLOW, env_file_allowances). A missing root is clean. Only paths come back, never content."""
    base = Path(root).absolute()
    if not base.exists():
        return []
    exact, patterns = _split_allow(allow)
    hits: List[str] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if _is_allowed(rel, path, exact, patterns):
                continue
            if contains_secret(name, needles):
                hits.append("name:%s" % rel)
            if path.is_symlink():
                if contains_secret(os.readlink(path), needles):
                    hits.append("link:%s" % rel)
                continue
            if stat.S_ISREG(os.lstat(path).st_mode):
                data = path.read_bytes()
                if any(needle.encode("utf-8") in data for needle in needles):
                    hits.append("file:%s" % rel)
    return hits


def scan_argv_logs(logs: Iterable[Union[ArgvStub, str, Path]], needles: Sequence[str] = (SECRET_SENTINEL,),
                   check_env: bool = False) -> List[str]:
    """`argv:<stub>[<call index>]` for every logged call whose argv holds a needle. With check_env also
    `env:<stub>[<index>]:<NAMES>` for calls whose environment held the sentinel (names only; an installer that was given
    the key as an environment variable may pass it down legitimately, so this is opt-in)."""
    hits: List[str] = []
    for item in logs:
        log = item.log if isinstance(item, ArgvStub) else Path(item)
        label = item.name if isinstance(item, ArgvStub) else log.stem
        for index, rec in enumerate(read_jsonl(log)):
            if contains_secret(rec.get("argv", []), needles):
                hits.append("argv:%s[%d]" % (label, index))
            if check_env and rec.get("env_with_sentinel"):
                hits.append("env:%s[%d]:%s" % (label, index, ",".join(rec["env_with_sentinel"])))
    return hits


def find_secret_leaks(*, results: Iterable[Any] = (), texts: Optional[Mapping[str, Any]] = None,
                      trees: Iterable[Union[str, Path]] = (), argv_logs: Iterable[Union[ArgvStub, str, Path]] = (),
                      allow: Iterable[Union[str, Path]] = (), needles: Sequence[str] = (SECRET_SENTINEL,),
                      check_env: bool = False) -> List[str]:
    """Every place a needle (default SECRET_SENTINEL) turned up; [] means clean. Locations only, never content.

    results: subprocess.CompletedProcess objects (Sandbox.run): stdout, stderr and the argv the test used are scanned,
    reported as `result[i].stdout` / `.stderr` / `.args`. texts: {label: str or bytes}. trees: directories scanned with
    scan_tree (the stack.env being provisioned is the one file that may hold the key: pass `allow`). argv_logs:
    ArgvStub objects or JSON-lines paths (stub_log_path), scanned with scan_argv_logs."""
    hits: List[str] = []
    for i, res in enumerate(results):
        for field in ("stdout", "stderr", "args"):
            if contains_secret(getattr(res, field, None), needles):
                hits.append("result[%d].%s" % (i, field))
    for label, text in (texts or {}).items():
        if contains_secret(text, needles):
            hits.append("text:%s" % label)
    allow_list = list(allow)
    for tree in trees:
        hits.extend("%s in %s" % (hit, tree) for hit in scan_tree(tree, needles, allow_list))
    hits.extend(scan_argv_logs(argv_logs, needles, check_env))
    return hits


def assert_no_secret_leak(**kw: Any) -> None:
    """Raise AssertionError naming the locations when find_secret_leaks(**kw) finds anything."""
    leaks = find_secret_leaks(**kw)
    if leaks:
        raise AssertionError("the secret sentinel leaked into: " + "; ".join(leaks))
