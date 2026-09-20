#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/scripts/setup-dev.sh"
LLVM_PATH="$ROOT/scripts/llvm-toolchain-path.sh"
# Physical path: llvm-toolchain-path.sh reports its answer through `pwd -P`, and
# on macOS `mktemp -d` hands back /var/... which is a symlink to /private/var/...,
# so a logical TMPD makes the bindir comparison below fail everywhere but Linux.
TMPD="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

plan_for() {
  local os="$1"
  local distro="${2:-}"
  SPROUT_SETUP_OS="$os" SPROUT_SETUP_DISTRO="$distro" \
    bash "$SETUP" --print-plan
}

ubuntu="$(plan_for linux ubuntu)"
grep -q 'apt-get install' <<<"$ubuntu" || fail "Ubuntu plan does not use apt-get"
grep -q 'llvm clang' <<<"$ubuntu" || fail "Ubuntu plan does not install LLVM and Clang"
grep -q 'build-essential' <<<"$ubuntu" || fail "Ubuntu plan lacks C build tools"
grep -Eq '(^|[[:space:]])opt([[:space:]]|$)' <<<"$ubuntu" \
  && fail "Ubuntu plan installs the unrelated GNU opt package"

fedora="$(plan_for linux fedora)"
grep -q 'dnf install' <<<"$fedora" || fail "Fedora plan does not use dnf"
grep -q 'llvm clang' <<<"$fedora" || fail "Fedora plan does not install LLVM and Clang"
grep -q 'gcc' <<<"$fedora" || fail "Fedora plan lacks C build tools"

arch="$(plan_for linux arch)"
grep -q 'pacman -S' <<<"$arch" || fail "Arch plan does not use pacman"
grep -q 'llvm clang' <<<"$arch" || fail "Arch plan does not install LLVM and Clang"
grep -q 'base-devel' <<<"$arch" || fail "Arch plan lacks C build tools"

opensuse="$(plan_for linux opensuse-tumbleweed)"
grep -Eq 'zypper .*install' <<<"$opensuse" || fail "openSUSE plan does not use zypper"
grep -q 'llvm clang' <<<"$opensuse" || fail "openSUSE plan does not install LLVM and Clang"

macos="$(plan_for macos)"
grep -q 'brew install' <<<"$macos" || fail "macOS plan does not use Homebrew"
grep -q 'mise llvm ripgrep' <<<"$macos" || fail "macOS plan lacks required tools"

if plan_for windows >"$TMPD/windows.out" 2>"$TMPD/windows.err"; then
  fail "native Windows was accepted as a supported host"
fi
grep -q 'not a supported host' "$TMPD/windows.err" \
  || fail "native Windows error does not explain support status"

mkdir -p "$TMPD/llvm/bin" "$TMPD/not-llvm/bin"
cat >"$TMPD/llvm/bin/opt" <<'TOOL'
#!/usr/bin/env bash
echo 'Debian LLVM version 18.1.3'
TOOL
cat >"$TMPD/llvm/bin/clang" <<'TOOL'
#!/usr/bin/env bash
echo 'Debian clang version 18.1.3'
TOOL
cat >"$TMPD/not-llvm/bin/opt" <<'TOOL'
#!/usr/bin/env bash
echo 'opt 3.19 -- a command line option parsing library'
TOOL
cat >"$TMPD/not-llvm/bin/clang" <<'TOOL'
#!/usr/bin/env bash
echo 'not clang'
TOOL
chmod +x "$TMPD"/llvm/bin/{opt,clang} "$TMPD"/not-llvm/bin/{opt,clang}

found="$(SPROUT_LLVM_BINDIR="$TMPD/llvm/bin" PATH=/usr/bin:/bin bash "$LLVM_PATH")"
[[ "$found" == "$TMPD/llvm/bin" ]] || fail "LLVM bindir override was not discovered"

found="$(SPROUT_LLVM_BINDIR="$TMPD/not-llvm/bin" PATH=/usr/bin:/bin bash "$LLVM_PATH")"
[[ -z "$found" ]] || fail "GNU opt was mistaken for LLVM opt"

mkdir -p "$TMPD/installed/bin"
cp "$TMPD/llvm/bin/opt" "$TMPD/llvm/bin/clang" "$TMPD/installed/bin/"
for tool in cc rg curl; do
  cat >"$TMPD/installed/bin/$tool" <<'TOOL'
#!/usr/bin/env bash
exit 0
TOOL
done
cat >"$TMPD/installed/bin/mise" <<'TOOL'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SPROUT_SETUP_TEST_LOG"
if [[ "$*" == "install gh" && "${MISE_AQUA_GITHUB_ATTESTATIONS:-true}" != false ]]; then exit 1; fi
exit 0
TOOL
chmod +x "$TMPD"/installed/bin/*

export SPROUT_SETUP_TEST_LOG="$TMPD/mise.log"
for run in 1 2; do
  output="$(
    SPROUT_SETUP_OS=linux \
    SPROUT_SETUP_DISTRO=ubuntu \
    SPROUT_LLVM_BINDIR="$TMPD/installed/bin" \
    SPROUT_SETUP_RETRY_DELAY=0 \
    PATH="$TMPD/installed/bin:/usr/bin:/bin" \
      bash "$SETUP" 2>&1
  )"
  grep -q 'System dependencies are already installed' <<<"$output" \
    || fail "idempotent run $run tried to reinstall system dependencies"
  grep -q 'development environment is ready' <<<"$output" \
    || fail "idempotent run $run did not finish setup"
done
[[ "$(grep -c '^trust mise.toml$' "$SPROUT_SETUP_TEST_LOG")" -eq 2 ]] \
  || fail "mise config was not trusted once per setup invocation"
[[ "$(grep -c '^install python just$' "$SPROUT_SETUP_TEST_LOG")" -eq 2 ]] \
  || fail "required mise tools were not installed once per setup invocation"
grep -q "attestation service is unavailable" <<<"$output" \
  || fail "GitHub CLI checksum fallback was not reported"
[[ "$(grep -c '^exec -- just install-hooks$' "$SPROUT_SETUP_TEST_LOG")" -eq 2 ]] \
  || fail "Git hook setup was not run once per setup invocation"
[[ "$(grep -c '^exec -- just bootstrap-from-seed$' "$SPROUT_SETUP_TEST_LOG")" -eq 2 ]] \
  || fail "compiler bootstrap was not run once per setup invocation"

echo "==> setup-dev platform and idempotency tests passed"
