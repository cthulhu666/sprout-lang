"""N-Queens: mutable backtracking — write True, recurse, write False.
skip is evaluated BEFORE mutation so it sees the unmodified arrays."""
import sys, time
sys.setrecursionlimit(5000)

def queens(n, row, col, cols, pd, nd):
    if row == n: return 1
    if col >= n: return 0
    p = row + col
    q = row - col + n - 1
    skip = queens(n, row, col + 1, cols, pd, nd)
    if not cols[col] and not pd[p] and not nd[q]:
        cols[col] = True; pd[p] = True; nd[q] = True
        place = queens(n, row + 1, 0, cols, pd, nd)
        cols[col] = False; pd[p] = False; nd[q] = False
    else:
        place = 0
    return skip + place

def count_solutions(n):
    return queens(n, 0, 0, [False]*n, [False]*(2*n-1), [False]*(2*n-1))

for n in [1, 4, 8, 10, 12, 13]:
    t = time.perf_counter()
    c = count_solutions(n)
    ms = (time.perf_counter() - t) * 1000
    print(f"N={n}: {c}  ({ms:.1f} ms)")
