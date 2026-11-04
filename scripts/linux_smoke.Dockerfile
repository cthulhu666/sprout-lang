# Linux toolchain image for `just linux-smoke` / `just linux-run` (see justfile).
#
# It deliberately matches CI: the same Ubuntu release (ci.yml `runs-on`) and the same LLVM,
# installed by the same scripts/ci_install_llvm.sh. `just ci-image-pin` fails if the distro
# on either side moves alone. Minor package versions (glibc patch level, LLVM point release)
# can still differ by build date, because apt serves whatever is current.
#
# Built from a minimal tar context (the justfile's `linux-image`): this file and
# ci_install_llvm.sh only, so the repo is never uploaded to the daemon. colima mounts $HOME
# over sshfs, and a multi-hundred-megabyte context costs minutes of pure overhead.
FROM ubuntu:26.04

# ci_install_llvm.sh needs curl and ca-certificates to fetch the apt.llvm.org key. Its
# apt-get call has no --no-install-recommends, so the apt.conf.d file keeps the image small.
RUN echo 'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/99no-recommends \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY scripts/ci_install_llvm.sh /tmp/ci_install_llvm.sh

# --sanitizers adds the ASan/UBSan runtime. Without it `-fsanitize=address,undefined` fails
# to LINK, and tests/c_runtime/run.sh falls back to an unsanitized build and still passes
# green, so two of its ten assertions silently stop being sanitized.
# ripgrep: the gc-safety and package-resolution gates. python3: scripts/runtime_word_casts.py.
# libsqlite3-dev, sqlite3 and openssl: twelve-dollar-example links the system libsqlite3,
# builds a database with the CLI, signs a JWT and calls the server.
RUN DEBIAN_FRONTEND=noninteractive bash /tmp/ci_install_llvm.sh --sanitizers \
      ripgrep libsqlite3-dev sqlite3 openssl python3 \
 && rm -rf /var/lib/apt/lists/* /tmp/ci_install_llvm.sh

# The recipes invoke BARE `clang` and `opt` from PATH, and only the versioned LLVM bindir
# (/usr/lib/llvm-N/bin) carries the unversioned names. The glob takes the highest installed
# bindir, so this line does not name the LLVM version that ci_install_llvm.sh pins.
RUN ln -s "$(ls -d /usr/lib/llvm-*/bin | sort -V | tail -n1)" /opt/llvm-bin
ENV PATH=/opt/llvm-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
