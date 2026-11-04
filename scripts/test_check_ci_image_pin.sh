#!/usr/bin/env bash
# check_ci_image_pin.sh against fixtures: every Ubuntu runs-on must equal the Dockerfile's FROM.
set -uo pipefail
cd "$(dirname "$0")/.."
SCRIPT=scripts/check_ci_image_pin.sh
TMPD=$(mktemp -d /tmp/sprout_ci_image_pin_XXXXXX)
trap 'rm -rf "$TMPD"' EXIT
fail=0
check() { # name want-exit got-exit
  if [[ "$2" != "$3" ]]; then echo "FAIL: $1 (exit $3, want $2)"; fail=1; else echo "ok: $1"; fi
}

GOOD_DOCKER=$'FROM ubuntu:26.04\nCOPY scripts/ci_install_llvm.sh /tmp/\nRUN /tmp/ci_install_llvm.sh --sanitizers\n'
# case name, ci.yml body (runs-on lines), Dockerfile body, want exit, text wanted on output
run_case() {
  local name="$1" ci="$2" docker="$3" want="$4" text="${5:-}"
  printf '%s' "$ci" > "$TMPD/ci.yml"
  printf '%s' "$docker" > "$TMPD/Dockerfile"
  local out rc
  out=$(bash "$SCRIPT" "$TMPD/ci.yml" "$TMPD/Dockerfile" 2>&1); rc=$?
  check "$name" "$want" "$rc"
  if [[ -n "$text" ]] && ! grep -qF -- "$text" <<<"$out"; then
    echo "FAIL: $name: output lacks '$text': $out"; fail=1
  fi
}

run_case "matching pin passes" $'jobs:\n  a:\n    runs-on: ubuntu-26.04\n' "$GOOD_DOCKER" 0 \
  "ci-image-pin ✓ (ubuntu 26.04)"
run_case "ubuntu-latest fails" $'    runs-on: ubuntu-latest\n' "$GOOD_DOCKER" 1 "ubuntu-latest"
run_case "different version fails" $'    runs-on: ubuntu-26.04\n    runs-on: ubuntu-24.04\n' \
  "$GOOD_DOCKER" 1 "ubuntu-24.04"
run_case "expression runs-on fails" $'    runs-on: ${{ matrix.os }}\n' "$GOOD_DOCKER" 1 'matrix.os'
run_case "no ubuntu runs-on fails" $'    runs-on: macos-latest\n' "$GOOD_DOCKER" 1 "no ubuntu"
run_case "no runs-on at all fails" $'name: ci\n' "$GOOD_DOCKER" 1 "no ubuntu"
run_case "FROM not ubuntu fails" $'    runs-on: ubuntu-26.04\n' \
  $'FROM debian:12\nRUN /tmp/ci_install_llvm.sh\n' 1 "FROM debian:12"
run_case "FROM missing fails" $'    runs-on: ubuntu-26.04\n' $'RUN /tmp/ci_install_llvm.sh\n' 1 "FROM"
run_case "two FROM lines fail" $'    runs-on: ubuntu-26.04\n' \
  $'FROM ubuntu:26.04\nFROM ubuntu:26.04\nRUN ci_install_llvm.sh\n' 1 "exactly one FROM"
run_case "Dockerfile without ci_install_llvm.sh fails" $'    runs-on: ubuntu-26.04\n' \
  $'FROM ubuntu:26.04\nRUN apt-get install -y clang\n' 1 "ci_install_llvm.sh"
run_case "COPY alone is not a use" $'    runs-on: ubuntu-26.04\n' \
  $'FROM ubuntu:26.04\nCOPY scripts/ci_install_llvm.sh /tmp/\n' 1 "ci_install_llvm.sh"
run_case "macos and windows beside a matching pin pass" \
  $'    runs-on: ubuntu-26.04\n    runs-on: macos-latest\n    runs-on: windows-latest\n' \
  "$GOOD_DOCKER" 0
run_case "commented-out ubuntu-latest is ignored" \
  $'    runs-on: ubuntu-26.04\n    # runs-on: ubuntu-latest\n' "$GOOD_DOCKER" 0

# The real tree.
bash "$SCRIPT"; rc=$?
check "real tree" 0 "$rc"

exit "$fail"
