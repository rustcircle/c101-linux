#!/usr/bin/env bash
# matrix.sh: run the installer inside containers, one per distribution, as a non-root user.
# Run it on a Linux host with rootless podman (docker only when you ask for it explicitly):
#   tests/matrix.sh                                  podman, every image, c101 from ../c101
#   CONTAINER_RUNTIME=docker tests/matrix.sh         the docker fallback
#   tests/matrix.sh ubuntu:24.04 debian:12           only these images
#   C101_SRC=/path/to/c101 tests/matrix.sh           another c101 checkout
# The c101 checkout and this repository are mounted READ-ONLY; c101's installer needs a writable tree (it creates
# .venv and restores executable bits), so each container works on a copy without .venv, .git and .stack.
# Per image: install (--profile ci), second install (idempotence), key hygiene, doctor, dry-run on a fresh copy (no changes),
# uninstall (the keys file stays).
# Keys: the run carries FAKE key values (FAL_KEY, EXA_API_KEY, JINA_API_KEY, SPIDER_API_KEY; fixed strings below, never a
# real key and never read from your environment). They exercise the key step and satisfy the doctor's FAIL rules for a
# missing key, which would otherwise fail every profile but ci (MATRIX_PROFILE=standard tests/matrix.sh). MATRIX_FAKE_KEYS=0
# runs without any, to see the unattended no-key path (warnings, no failure in ci).
# Output: a PASS/FAIL/SKIP table and one log per image under .stack/out/matrix/<timestamp>/. Exit 1 on any FAIL.
set -Eeuo pipefail

root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
RT=${CONTAINER_RUNTIME:-podman}
C101_SRC=${C101_SRC:-$root/../c101}
DEFAULT_IMAGES=(ubuntu:24.04 ubuntu:22.04 debian:12 fedora:41 archlinux:latest alpine:3.20)
PROFILE=${MATRIX_PROFILE:-ci}
TIMEOUT=${MATRIX_TIMEOUT:-1800}
# Fixed fake values, all starting with the prefix the key-hygiene step greps for. They are constants on purpose: a value
# from the caller's environment (a real FAL_KEY, say) must never be forwarded into a container.
FAKE_KEYS=${MATRIX_FAKE_KEYS:-1}
fake_env=()
if [ "$FAKE_KEYS" = 1 ]; then
  fake_env=(
    -e FAL_KEY=c101l-matrix-fake-fal-not-a-key
    -e EXA_API_KEY=c101l-matrix-fake-exa-not-a-key
    -e JINA_API_KEY=c101l-matrix-fake-jina-not-a-key
    -e SPIDER_API_KEY=c101l-matrix-fake-spider-not-a-key
  )
fi

case $RT in podman | docker) ;; *)
  echo "CONTAINER_RUNTIME must be podman or docker" >&2
  exit 2
  ;;
esac
command -v "$RT" >/dev/null 2>&1 || {
  echo "matrix.sh: $RT not found (podman is the default; set CONTAINER_RUNTIME=docker to use docker)" >&2
  exit 2
}
[ -f "$C101_SRC/install.sh" ] || {
  echo "matrix.sh: no c101 checkout at $C101_SRC (set C101_SRC)" >&2
  exit 2
}
C101_SRC=$(cd -P -- "$C101_SRC" && pwd -P)

if [ "$#" -gt 0 ]; then images=("$@"); else images=("${DEFAULT_IMAGES[@]}"); fi

host_arch=$(uname -m)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$root/.stack/out/matrix/$stamp"
mkdir -p "$out"

# Mount options: ro everywhere; z relabels for SELinux hosts and is harmless elsewhere.
mounts=(-v "$C101_SRC:/c101-ro:ro,z" -v "$root:/overlay-ro:ro,z")

# The script that runs inside the container. Root phase: prerequisites, a user, writable copies. Then the
# user phase through su. Written for sh so Alpine's busybox can start it; it installs bash first.
read -r -d '' inner <<'INNER_EOF' || true
set -eu
. /etc/os-release
case " $ID ${ID_LIKE:-} " in
  *" alpine "*) apk add --no-cache bash curl git ca-certificates shadow procps tar >/dev/null ;;
  *" arch "*) pacman -Sy --noconfirm --needed bash curl git ca-certificates procps-ng tar >/dev/null ;;
  *" debian "* | *" ubuntu "*) apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends bash curl git ca-certificates procps tar >/dev/null ;;
  *" fedora "* | *" rhel "*) dnf install -y -q bash curl git ca-certificates procps-ng tar shadow-utils util-linux >/dev/null ;;
  *) echo "matrix: no bootstrap for $ID" >&2; exit 90 ;;
esac
if command -v useradd >/dev/null 2>&1; then useradd -m -s /bin/bash tester; else adduser -D -s /bin/bash tester; fi
copy() { mkdir -p "$2" && tar -C "$1" --exclude=.venv --exclude=.git --exclude=.stack --exclude=__pycache__ -cf - . | tar -C "$2" -xf -; chown -R tester "$2"; }
copy /c101-ro /home/tester/c101
copy /overlay-ro /home/tester/overlay
copy /c101-ro /home/tester/c101-dry
chmod +x /home/tester/overlay/install-linux.sh /home/tester/overlay/uninstall-linux.sh /home/tester/overlay/bin/*
cat > /home/tester/snap.sh <<'SNAP'
#!/bin/sh
P='-path /home/tester/c101 -o -path /home/tester/overlay -o -path /home/tester/snap.sh'
find /home/tester \( $P \) -prune -o -print | sort
find /home/tester \( $P \) -prune -o -type f -exec cksum {} + | sort
SNAP
chmod 755 /home/tester/snap.sh
keyenv=""
for n in FAL_KEY EXA_API_KEY JINA_API_KEY SPIDER_API_KEY; do
  eval "v=\${$n:-}"
  if [ -n "$v" ]; then keyenv="$keyenv $n='$v'"; fi
done
run_as() { su tester -s /bin/bash -c "export HOME=/home/tester USER=tester PROFILE='$PROFILE'$keyenv && cd /home/tester && $1"; }
echo "### install"
run_as '/home/tester/overlay/install-linux.sh --profile "$PROFILE" --source /home/tester/c101 --yes'
echo "### install again (idempotence)"
run_as '/home/tester/overlay/install-linux.sh --profile "$PROFILE" --source /home/tester/c101 --yes'
if [ -n "$keyenv" ]; then
  echo "### keys: stack.env is mode 600 and holds no key outside it"
  run_as 'test "$(stat -c %a /home/tester/.claude/stack.env)" = 600'
  # every fake value starts with c101l-matrix-fake: the key step, the login profile, environment.d, units, the settings
  # file and the fal-runner agent must not carry one (only stack.env and its backups may)
  run_as 'for p in .profile .bash_profile .bash_login .zprofile .config .local/state .local/bin .claude/settings.json .claude/agents/fal-runner.md; do if [ -e "$p" ] && grep -rqF -e c101l-matrix-fake -- "$p"; then echo "a key value was written to $p" >&2; exit 1; fi; done'
fi
echo "### doctor"
run_as '/home/tester/overlay/bin/c101-linux-doctor --profile "$PROFILE" --skip-c101 --offline'
echo "### dry run changes nothing"
run_as '/home/tester/snap.sh > /tmp/before.txt; /home/tester/overlay/install-linux.sh --profile full --dry-run --source /home/tester/c101-dry >/dev/null; /home/tester/snap.sh > /tmp/after.txt; diff /tmp/before.txt /tmp/after.txt'
echo "### uninstall"
run_as '/home/tester/overlay/uninstall-linux.sh --yes'
echo "### the keys file survives the uninstall"
run_as 'test -f /home/tester/.claude/stack.env'
echo "### done"
INNER_EOF

printf '%-18s %-6s %s\n' IMAGE RESULT NOTE >"$out/summary.txt"
fails=0
for image in "${images[@]}"; do
  slug=${image//[:\/]/_}
  log="$out/$slug.log"
  platform_args=()
  # archlinux publishes amd64 only: skip cleanly on arm64 hosts instead of failing the whole run.
  if [ "$image" = archlinux:latest ] && [ "$host_arch" != x86_64 ] && [ "$host_arch" != amd64 ]; then
    printf '%-18s %-6s %s\n' "$image" SKIP "archlinux has no $host_arch image" | tee -a "$out/summary.txt"
    continue
  fi
  if ! "$RT" pull -q "$image" >"$log" 2>&1; then
    printf '%-18s %-6s %s\n' "$image" SKIP "image not pullable here (see $slug.log)" | tee -a "$out/summary.txt"
    continue
  fi
  start=$(date +%s)
  if timeout "$TIMEOUT" "$RT" run --rm ${platform_args[@]+"${platform_args[@]}"} "${mounts[@]}" \
    -e C101L_UV_VERSION="${C101L_UV_VERSION:-}" ${fake_env[@]+"${fake_env[@]}"} "$image" sh -c "PROFILE=$PROFILE; $inner" >>"$log" 2>&1; then
    # Key values are never printed: not by the installer, the doctor, the dry run or the uninstaller.
    if [ "$FAKE_KEYS" = 1 ] && grep -qF c101l-matrix-fake "$log"; then
      fails=$((fails + 1))
      printf '%-18s %-6s %s\n' "$image" FAIL "a fake key value appeared in the output (see $slug.log); only key names may be printed" | tee -a "$out/summary.txt"
    else
      printf '%-18s %-6s %s\n' "$image" PASS "$(($(date +%s) - start))s" | tee -a "$out/summary.txt"
    fi
  else
    rc=$?
    fails=$((fails + 1))
    printf '%-18s %-6s %s\n' "$image" FAIL "exit $rc after $(($(date +%s) - start))s; tail of $slug.log: $(tail -n 1 "$log" | cut -c1-100)" | tee -a "$out/summary.txt"
  fi
done

echo
echo "runtime: $RT   profile: $PROFILE   fake keys: $([ "$FAKE_KEYS" = 1 ] && echo yes || echo no)   logs: $out"
[ "$fails" -eq 0 ]
