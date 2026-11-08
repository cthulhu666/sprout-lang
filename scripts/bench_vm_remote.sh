#!/usr/bin/env bash
# Runs ON the benchmark VM (Ubuntu, normal user with passwordless sudo); started by bench_vm.sh.
#
#   BENCH_ROUNDS=9 bash bench_vm_remote.sh base.tar head.tar <base-sha> <head-sha>
#
# Builds A from base's committed seed and runtime and B from head's: each tree's own compiler,
# codegen and runtime ABI, as that tree ships them. Then checks their IR, times both compiling
# base's compiler, and writes ~/results.txt.
set -euo pipefail

# ── parsing helpers (unit-tested by scripts/test_bench_vm.sh) ───────────────

# Wall seconds from a GNU `time -v` report: "... (h:mm:ss or m:ss): 1:02:03.50".
time_wall_secs() {
  awk '/Elapsed \(wall clock\)/ {
    t = $0; sub(/.*\): /, "", t)
    n = split(t, p, ":")
    s = (n == 3) ? p[1] * 3600 + p[2] * 60 + p[3] : p[1] * 60 + p[2]
    printf "%.2f\n", s
  }' "$1"
}

# Peak resident set size in KiB from a GNU `time -v` report.
time_rss_kb() {
  awk '/Maximum resident set size/ { print $NF }' "$1"
}

# Median / min / max of the numbers on stdin, two decimals.
stat_median() {
  sort -n | awk '{ a[NR] = $1 } END {
    if (NR == 0) exit 1
    if (NR % 2) printf "%.2f\n", a[(NR + 1) / 2]
    else printf "%.2f\n", (a[NR / 2] + a[NR / 2 + 1]) / 2
  }'
}
stat_min() { sort -n | awk 'NR == 1 { printf "%.2f\n", $1 }'; }
stat_max() { sort -n | awk '{ v = $1 } END { if (NR) printf "%.2f\n", v }'; }

# Side-by-side counters from two files holding the runtime's SPROUT_DEBUG_ALLOC line
# (one stderr line, printed at exit: "[sprout alloc] sprout_obj=N closure=N ... closure_apply=N").
alloc_table() {
  awk 'FILENAME == ARGV[1] { f = 1 } FILENAME == ARGV[2] { f = 2 }
    /^\[sprout alloc\]/ {
      for (i = 3; i <= NF; i++) {
        split($i, kv, "=")
        if (!(kv[1] in seen)) { seen[kv[1]] = 1; order[++n] = kv[1] }
        v[f, kv[1]] = kv[2]
      }
    }
    END { for (i = 1; i <= n; i++) printf "%-16s %14s %14s\n", order[i], v[1, order[i]], v[2, order[i]] }' "$1" "$2"
}

if [ -n "${BENCH_REMOTE_LIB_ONLY:-}" ]; then return 0; fi

# ── main ─────────────────────────────────────────────────────────────────────
BASE_TAR="${1:?base tarball}"
HEAD_TAR="${2:?head tarball}"
BASE_SHA="${3:?base sha}"
HEAD_SHA="${4:?head sha}"
ROUNDS="${BENCH_ROUNDS:-9}"

cd "$HOME"
echo "==> installing packages"
# A fresh image runs apt jobs in the background: they hold the dpkg lock and load the machine.
sudo systemctl stop unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
sudo apt-get -o DPkg::Lock::Timeout=300 update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -qq clang llvm time
LLVM_BINDIR="$(ls -d /usr/lib/llvm-*/bin 2>/dev/null | sort -V | tail -n1 || true)"
if [ -n "$LLVM_BINDIR" ]; then PATH="$LLVM_BINDIR:$PATH"; fi
export PATH

echo "==> extracting"
rm -rf base head
mkdir base head
tar -xf "$BASE_TAR" -C base
tar -xf "$HEAD_TAR" -C head

# Emitted IR needs a clang that lowers __int128 returns as i128; without it both sides skew.
echo "==> checking clang"
(cd base && bash scripts/check_clang_cpr.sh)

echo "==> building A (base's seed and runtime) and B (head's)"
clang base/bootstrap/compile_driver.ll base/runtime/*.c -O2 -o A
clang head/bootstrap/compile_driver.ll head/runtime/*.c -O2 -o B

echo "==> IR identity"
IR_LINES=""
for input in base head; do
  ./A --emit-ir "$input/stdlib" "$input/stdlib/compiler/compile_driver.sprout" > "ir_A_$input.ll"
  ./B --emit-ir "$input/stdlib" "$input/stdlib/compiler/compile_driver.sprout" > "ir_B_$input.ll"
  if cmp -s "ir_A_$input.ll" "ir_B_$input.ll"; then verdict="identical IR"; else verdict="IR DIFFERS"; fi
  IR_LINES="$IR_LINES$(printf 'A vs B compiling %-4s compiler: %s' "$input" "$verdict")
"
done

WORK_ARGS=(--emit-ir base/stdlib base/stdlib/compiler/compile_driver.sprout)
run_timed() { # run_timed <A|B> <time-file>
  local err="$2.err"
  if ! /usr/bin/time -v -o "$2" "./$1" "${WORK_ARGS[@]}" > /dev/null 2> "$err"; then
    cat "$err" >&2
    echo "bench_vm_remote: compiler $1 failed on the timed workload" >&2
    return 1
  fi
}

# The builds above load the machine themselves; let the 1-minute average settle before timing.
echo "==> waiting for load to settle"
for _ in $(seq 1 24); do
  if awk '{ exit !($1 <= 0.5) }' /proc/loadavg; then break; fi
  sleep 5
done

echo "==> timing ($ROUNDS interleaved rounds)"
LOAD_BEFORE="$(cut -d' ' -f1-3 /proc/loadavg)"
LOAD_BUSY="$(awk '{ print ($1 > 0.5) ? 1 : 0 }' /proc/loadavg)"
: > walls_A; : > walls_B; : > rss_A; : > rss_B
ROUND_LINES=""
r=1
while [ "$r" -le "$ROUNDS" ]; do
  if [ $((r % 2)) -eq 1 ]; then order="A B"; else order="B A"; fi
  for x in $order; do
    run_timed "$x" "time_$x.txt"
    time_wall_secs "time_$x.txt" >> "walls_$x"
    time_rss_kb "time_$x.txt" >> "rss_$x"
  done
  ROUND_LINES="$ROUND_LINES$(printf 'round %2d  order %s  A %8s s  B %8s s' "$r" "$(echo "$order" | tr -d ' ')" "$(tail -n1 walls_A)" "$(tail -n1 walls_B)")
"
  r=$((r + 1))
done
LOAD_AFTER="$(cut -d' ' -f1-3 /proc/loadavg)"

echo "==> allocation counters"
SPROUT_DEBUG_ALLOC=1 ./A "${WORK_ARGS[@]}" > /dev/null 2> alloc_A.txt
SPROUT_DEBUG_ALLOC=1 ./B "${WORK_ARGS[@]}" > /dev/null 2> alloc_B.txt

rss_median_mib() { sort -n "$1" | awk '{ a[NR] = $1 } END {
  m = (NR % 2) ? a[(NR + 1) / 2] : (a[NR / 2] + a[NR / 2 + 1]) / 2
  printf "%.1f\n", m / 1024 }'; }

{
  echo "base  $BASE_SHA"
  echo "head  $HEAD_SHA"
  echo "cpus  $(nproc)"
  echo "model $(lscpu | awk -F: '/Model name/ { gsub(/^ +/, "", $2); print $2; exit }')"
  echo "uname $(uname -r)"
  echo "clang $(clang --version | head -n1)"
  echo "load before  $LOAD_BEFORE"
  echo "load after   $LOAD_AFTER"
  if [ "$LOAD_BUSY" -eq 1 ]; then echo "WARNING: load ${LOAD_BEFORE%% *} at start, machine not quiet"; fi
  echo
  printf '%s' "$IR_LINES"
  echo
  echo "workload: compile base's compiler ($ROUNDS rounds, odd rounds A then B, even rounds B then A)"
  printf '%s' "$ROUND_LINES"
  echo
  printf '%-12s %10s %10s\n' "wall (s)" "A" "B"
  printf '%-12s %10s %10s\n' "median" "$(stat_median < walls_A)" "$(stat_median < walls_B)"
  printf '%-12s %10s %10s\n' "min" "$(stat_min < walls_A)" "$(stat_min < walls_B)"
  printf '%-12s %10s %10s\n' "max" "$(stat_max < walls_A)" "$(stat_max < walls_B)"
  printf '%-12s %10s %10s\n' "med RSS MiB" "$(rss_median_mib rss_A)" "$(rss_median_mib rss_B)"
  echo
  printf '%-16s %14s %14s\n' "alloc counter" "A" "B"
  alloc_table alloc_A.txt alloc_B.txt
} > "$HOME/results.txt"
echo "==> wrote $HOME/results.txt"
