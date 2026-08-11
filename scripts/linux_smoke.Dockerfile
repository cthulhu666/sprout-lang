# Linux toolchain image for `just linux-smoke` / `just linux-run` (see justfile).
#
# The point of this image is FIDELITY TO CI, not novelty: it deliberately mirrors the
# "Install LLVM/Clang + ripgrep" step of .github/workflows/ci.yml — same distro, same
# glibc, LLVM from the same apt source — so a local Linux run and the CI run diverge
# as little as possible. A pinned third-party clang image would fix the compiler
# version while floating the distro and libc, which is the wrong axis to pin for a
# gate that exists to reproduce CI.
#
# Consequence, stated plainly: `apt-get install llvm clang` is not reproducible across
# time. That is intentional here. The gate drifts WITH ubuntu-latest instead of
# independently of it; a version-locked image would eventually certify a toolchain CI
# no longer uses, which is the failure mode this whole gate exists to prevent.
#
# Built with an EMPTY context (`docker build -t … - < this-file`), so the repo is never
# uploaded to the daemon. That matters: colima mounts $HOME over sshfs, and shipping a
# multi-hundred-megabyte build context across it costs minutes of pure overhead.
FROM ubuntu:24.04

# ripgrep is not needed by task-io-smoke; it is here so the other gates (gc-safety,
# package-resolution) are reachable through `just linux-run` without a rebuild.
# ca-certificates lets the pinned-`just` download run from inside the container if the
# host ever needs it to.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      llvm clang ripgrep ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The recipes invoke BARE `clang` and `opt` from PATH, and only the versioned LLVM
# bindir (/usr/lib/llvm-N/bin) carries the unversioned names. Same "pick the highest
# installed bindir" rule as CI, for the same reason: pinning llvm-N here would break
# whenever the ubuntu:24.04 image's default LLVM moves.
RUN ln -s "$(ls -d /usr/lib/llvm-*/bin | sort -V | tail -n1)" /opt/llvm-bin
ENV PATH=/opt/llvm-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
