#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRINT_PLAN=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/setup-dev.sh [--print-plan]

Install Sprout's development toolchain and bootstrap the compiler.
--print-plan prints the platform package commands without changing the system.
USAGE
}

case "${1:-}" in
  "") ;;
  --print-plan) PRINT_PLAN=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

case "${SPROUT_SETUP_OS:-$(uname -s)}" in
  Darwin|darwin|macos) platform=macos ;;
  Linux|linux) platform=linux ;;
  MINGW*|MSYS*|CYGWIN*|windows)
    echo "Native Windows is not a supported host yet; use WSL or see docs/windows-port-v0.md." >&2
    exit 1
    ;;
  *)
    echo "This operating system is not a supported host. Sprout supports macOS and Linux." >&2
    exit 1
    ;;
esac

linux_distro() {
  if [[ -n "${SPROUT_SETUP_DISTRO:-}" ]]; then
    printf '%s\n' "$SPROUT_SETUP_DISTRO"
    return
  fi
  [[ -r /etc/os-release ]] || return 1
  # shellcheck disable=SC1091
  source /etc/os-release
  printf '%s %s\n' "${ID:-}" "${ID_LIKE:-}"
}

linux_manager() {
  local distro="$1"
  case " $distro " in
    *" ubuntu "*|*" debian "*) echo apt ;;
    *" fedora "*|*" rhel "*|*" centos "*) echo dnf ;;
    *" arch "*|*" manjaro "*) echo pacman ;;
    *" opensuse "*|*" opensuse-tumbleweed "*|*" suse "*) echo zypper ;;
    *) return 1 ;;
  esac
}

print_plan() {
  if [[ "$platform" == macos ]]; then
    cat <<'PLAN'
Install Homebrew if it is missing
brew install mise llvm ripgrep
mise trust mise.toml
mise install python just
mise install gh  # retries, then falls back to checksum verification
mise exec -- just install-hooks bootstrap-from-seed
PLAN
    return
  fi

  local distro manager
  distro="$(linux_distro)"
  if ! manager="$(linux_manager "$distro")"; then
    echo "Unsupported Linux distribution: $distro" >&2
    exit 1
  fi
  case "$manager" in
    apt)
      echo "sudo apt-get update"
      echo "sudo apt-get install -y llvm clang libclang-rt-dev build-essential ripgrep curl ca-certificates git"
      ;;
    dnf)
      echo "sudo dnf install -y llvm clang compiler-rt gcc gcc-c++ make ripgrep curl ca-certificates git"
      ;;
    pacman)
      echo "sudo pacman -Sy --needed llvm clang compiler-rt base-devel ripgrep curl ca-certificates git"
      ;;
    zypper)
      echo "sudo zypper --non-interactive install llvm clang gcc make ripgrep curl ca-certificates git"
      ;;
  esac
  echo "curl https://mise.run | sh"
  echo "mise trust mise.toml"
  echo "mise install python just"
  echo "mise install gh  # retries, then falls back to checksum verification"
  echo "mise exec -- just install-hooks bootstrap-from-seed"
}

if ((PRINT_PLAN)); then
  print_plan
  exit 0
fi

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "sudo is required to install system packages." >&2
    exit 1
  fi
}

install_linux_packages() {
  local distro manager
  distro="$(linux_distro)"
  if ! manager="$(linux_manager "$distro")"; then
    echo "Unsupported Linux distribution: $distro" >&2
    echo "Install LLVM >= 16, Clang, C build tools, ripgrep, curl, and mise, then rerun." >&2
    exit 1
  fi

  echo "==> Installing system dependencies with $manager..."
  case "$manager" in
    apt)
      run_as_root apt-get update
      run_as_root apt-get install -y llvm clang libclang-rt-dev build-essential ripgrep curl ca-certificates git
      ;;
    dnf)
      run_as_root dnf install -y llvm clang compiler-rt gcc gcc-c++ make ripgrep curl ca-certificates git
      ;;
    pacman)
      run_as_root pacman -Sy --needed llvm clang compiler-rt base-devel ripgrep curl ca-certificates git
      ;;
    zypper)
      run_as_root zypper --non-interactive install llvm clang gcc make ripgrep curl ca-certificates git
      ;;
  esac
}

install_homebrew() {
  if command -v brew >/dev/null 2>&1; then
    return
  fi
  echo "==> Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    echo "Homebrew installed but could not be located." >&2
    exit 1
  fi
}

install_mise() {
  if command -v mise >/dev/null 2>&1; then
    return
  fi
  echo "==> Installing mise..."
  curl -fsSL https://mise.run | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v mise >/dev/null 2>&1 || {
    echo "mise installed but was not found at $HOME/.local/bin." >&2
    exit 1
  }
}

install_gh() {
  local attempt
  for attempt in 1 2 3; do
    if mise install gh; then
      return
    fi
    if ((attempt < 3)); then
      echo "==> GitHub CLI install failed; retrying ($attempt/3)..." >&2
      sleep "${SPROUT_SETUP_RETRY_DELAY:-2}"
    fi
  done

  echo "==> GitHub's attestation service is unavailable; retrying with checksum verification." >&2
  if MISE_AQUA_GITHUB_ATTESTATIONS=false mise install gh; then
    return
  fi
  echo "GitHub CLI installation failed with both attestation and checksum verification." >&2
  exit 1
}

if [[ "$platform" == macos ]]; then
  command -v xcode-select >/dev/null 2>&1 && xcode-select -p >/dev/null 2>&1 || {
    echo "Install Xcode Command Line Tools with 'xcode-select --install', then rerun." >&2
    exit 1
  }
  install_homebrew
  echo "==> Installing Homebrew dependencies..."
  brew install mise llvm ripgrep
else
  llvm_before="$(bash "$ROOT/scripts/llvm-toolchain-path.sh")"
  if [[ -z "$llvm_before" ]] || ! command -v cc >/dev/null 2>&1 \
      || ! command -v rg >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    install_linux_packages
  else
    echo "==> System dependencies are already installed."
  fi
  install_mise
fi

llvm_bindir="$(bash "$ROOT/scripts/llvm-toolchain-path.sh")"
if [[ -z "$llvm_bindir" ]]; then
  echo "Could not find matching LLVM opt and clang binaries (LLVM >= 16)." >&2
  exit 1
fi
export PATH="$llvm_bindir:$PATH"

echo "==> Using $($llvm_bindir/opt --version | sed -n '1p')"
echo "==> Installing repository-managed tools..."
cd "$ROOT"
mise trust mise.toml
mise install python just
install_gh
mise exec -- just install-hooks
mise exec -- just bootstrap-from-seed

cat <<'DONE'
==> Sprout development environment is ready.
    Run: mise exec -- just test
DONE
