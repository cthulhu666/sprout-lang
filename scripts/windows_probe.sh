#!/usr/bin/env bash
# Windows port W0a — measure what the target actually provides.
#
# The runtime is POSIX-only (docs/windows-port-v0.md). Before porting anything, this
# reports which of the headers and functions it uses exist for the Windows target, so
# the work list is measured rather than assumed.
#
# Why probe each header SEPARATELY instead of just compiling the runtime: a missing
# #include is a *fatal* error that aborts the translation unit, so a plain compile
# reports exactly one blocker per run and hides every later one. Probing each header
# on its own yields the whole matrix in one pass.
#
# Why take each function's ADDRESS: a header can exist while the function does not
# (mingw-w64 ships signal.h without POSIX sigaction). `&fn` fails to compile when the
# declaration is absent, and does not silently accept a signature mismatch.
#
# Requires a mingw-w64 sysroot: `brew install mingw-w64`, or set SPROUT_MINGW_SYSROOT.
# Read-only: compiles to nothing (-fsyntax-only) and writes only under a temp dir.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SYSROOT="${SPROUT_MINGW_SYSROOT:-}"
if [ -z "$SYSROOT" ]; then
  if prefix="$(brew --prefix mingw-w64 2>/dev/null)"; then
    SYSROOT="$prefix/toolchain-x86_64/x86_64-w64-mingw32"
  fi
fi
if [ -z "$SYSROOT" ] || [ ! -d "$SYSROOT" ]; then
  echo "[windows-probe] no mingw-w64 sysroot found." >&2
  echo "  Install it (brew install mingw-w64) or set SPROUT_MINGW_SYSROOT to a sysroot" >&2
  echo "  containing include/windows.h. Looked at: ${SYSROOT:-<unset>}" >&2
  exit 1
fi

CC=(clang --target=x86_64-w64-mingw32 --sysroot="$SYSROOT" -fsyntax-only)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0

probe_header() {
  printf '#include <%s>\nint main(void){return 0;}\n' "$1" > "$TMP/p.c"
  if "${CC[@]}" "$TMP/p.c" >/dev/null 2>&1; then
    printf '  ok       %s\n' "$1"; pass=$((pass + 1))
  else
    printf '  MISSING  %s\n' "$1"; fail=$((fail + 1))
  fi
}

probe_fn() {   # probe_fn <header> <function>
  printf '#include <%s>\nvoid* p(void){ return (void*)&%s; }\n' "$1" "$2" > "$TMP/p.c"
  if "${CC[@]}" "$TMP/p.c" >/dev/null 2>&1; then
    printf '  ok       %-28s (%s)\n' "$2" "$1"; pass=$((pass + 1))
  else
    printf '  MISSING  %-28s (%s)\n' "$2" "$1"; fail=$((fail + 1))
  fi
}

echo "==> sysroot: $SYSROOT"
echo
echo "== Headers the runtime includes today"
for h in stdio.h stdlib.h stdint.h limits.h stdarg.h string.h regex.h \
         sys/socket.h sys/types.h netinet/in.h arpa/inet.h netdb.h errno.h \
         sys/time.h time.h sys/wait.h poll.h fcntl.h signal.h termios.h \
         unistd.h execinfo.h pthread.h stdatomic.h sys/resource.h sys/mman.h \
         ucontext.h sys/event.h sys/epoll.h sys/timerfd.h; do
  probe_header "$h"
done

echo
echo "== Headers the port needs"
for h in windows.h winsock2.h ws2tcpip.h dbghelp.h; do probe_header "$h"; done

echo
echo "== POSIX functions, where the header exists"
for f in signal raise sigaction sigaltstack sigemptyset; do probe_fn signal.h "$f"; done
for f in read write close pipe fork execvp readlink isatty dup2 getpid; do probe_fn unistd.h "$f"; done
for f in pthread_create pthread_detach pthread_mutex_lock pthread_cond_wait; do probe_fn pthread.h "$f"; done
for f in clock_gettime nanosleep; do probe_fn time.h "$f"; done
for f in ftello fseeko _ftelli64 _fseeki64; do probe_fn stdio.h "$f"; done

echo
echo "== Win32 replacements the design names (docs/windows-port-v0.md §4, §6)"
for f in WSAPoll WSAStartup closesocket ioctlsocket WSAGetLastError; do probe_fn winsock2.h "$f"; done
for f in ConvertThreadToFiber CreateFiber SwitchToFiber DeleteFiber \
         VirtualAlloc VirtualFree SetConsoleMode GetConsoleMode \
         GetModuleFileNameA CreateProcessA CreatePipe CaptureStackBackTrace \
         AddVectoredExceptionHandler CreateWaitableTimerA; do
  probe_fn windows.h "$f"
done

echo
echo "== Runtime translation units (first blocker each; missing headers are fatal)"
for tu in sprout_poll.c sprout_scheduler.c sprout_runtime.c; do
  printf '  %-22s ' "$tu"
  if "${CC[@]}" "$ROOT/runtime/$tu" >"$TMP/err" 2>&1; then
    echo "compiles"
  else
    # The first LINE is often "In file included from …" — a note, not the blocker. Take the
    # first line that actually says `error:`, falling back to line 1 if clang worded it some
    # other way.
    { grep -m1 'error:' "$TMP/err" || head -1 "$TMP/err"; } | sed "s|$ROOT/||"
  fi
done

echo
echo "==> $pass available, $fail missing"
