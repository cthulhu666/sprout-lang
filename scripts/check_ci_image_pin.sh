#!/usr/bin/env bash
# Fail unless every Ubuntu `runs-on:` in ci.yml names the same version as the linux-smoke
# image's FROM line, and the image installs LLVM through ci_install_llvm.sh like CI does
# (docs/gates.md §`just linux-smoke`). macos-* and windows-* runners are not checked.
#
# usage: check_ci_image_pin.sh [ci.yml] [Dockerfile]
set -uo pipefail
cd "$(dirname "$0")/.."

ci="${1:-.github/workflows/ci.yml}"
dockerfile="${2:-scripts/linux_smoke.Dockerfile}"

fail() { echo "FAIL: ci-image-pin: $1" >&2; exit 1; }

[[ -r "$ci" ]] || fail "cannot read $ci"
[[ -r "$dockerfile" ]] || fail "cannot read $dockerfile"

froms=$(grep -E '^[[:space:]]*FROM[[:space:]]' "$dockerfile" || true)
if [[ -z "$froms" ]] || (($(wc -l <<<"$froms") != 1)); then
  fail "$dockerfile needs exactly one FROM line, found: ${froms:-none}"
fi
if [[ ! "$froms" =~ ^[[:space:]]*FROM[[:space:]]+ubuntu:([0-9][0-9.]*)[[:space:]]*$ ]]; then
  fail "$dockerfile FROM is not 'FROM ubuntu:<version>': $froms"
fi
version="${BASH_REMATCH[1]}"

ubuntu_seen=0
while IFS= read -r line; do
  val="${line#*runs-on:}"
  val="${val%%#*}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  val="${val#[\'\"]}"
  val="${val%[\'\"]}"
  case "$val" in
    macos-* | windows-*) continue ;;
    ubuntu-*) ubuntu_seen=1 ;;
  esac
  [[ "$val" == "ubuntu-$version" ]] \
    || fail "$ci runs-on '$val' != ubuntu-$version (from $dockerfile)"
done < <(grep -E '^[[:space:]]*runs-on:' "$ci")

((ubuntu_seen)) || fail "$ci has no ubuntu runs-on line"

# A COPY of the script is not a use of it: skip comment, COPY and ADD lines.
grep -vE '^[[:space:]]*(#|COPY[[:space:]]|ADD[[:space:]])' "$dockerfile" | grep -q 'ci_install_llvm\.sh' \
  || fail "$dockerfile has no RUN using ci_install_llvm.sh"

echo "==> ci-image-pin ✓ (ubuntu $version)"
