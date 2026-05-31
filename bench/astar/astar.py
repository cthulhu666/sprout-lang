"""A* on a 100x100 grid — heapq open set, mutable list arrays.
Uses the same wall rule as the Sprout implementation.
g_score and closed use flat lists indexed by y*W+x for O(1) access.
heapq gives O(log n) push/pop (vs Sprout's O(n) sorted-list insert).
"""
import heapq
import time

W = 100
GOAL_X, GOAL_Y = 99, 99
INF = 9_999_999
ITERS = 200
DIRS = [(0, -1), (0, 1), (-1, 0), (1, 0)]


def is_wall(x, y):
    if x <= 0 or y <= 0 or x >= GOAL_X or y >= GOAL_Y:
        return False
    return (x * 5 + y * 3) % 13 < 4


def h(x, y):
    return abs(GOAL_X - x) + abs(GOAL_Y - y)


def astar():
    g_score = [INF] * (W * W)
    closed = [False] * (W * W)
    g_score[0] = 0
    # heap entries: (f, g, x, y)
    open_heap = [(h(0, 0), 0, 0, 0)]

    while open_heap:
        _, g, x, y = heapq.heappop(open_heap)
        if x == GOAL_X and y == GOAL_Y:
            return g
        idx = y * W + x
        if closed[idx]:
            continue
        closed[idx] = True

        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < W and 0 <= ny < W):
                continue
            nidx = ny * W + nx
            if is_wall(nx, ny) or closed[nidx]:
                continue
            ng = g + 1
            if ng < g_score[nidx]:
                g_score[nidx] = ng
                heapq.heappush(open_heap, (ng + h(nx, ny), ng, nx, ny))

    return -1


# warmup
for _ in range(5):
    astar()

t0 = time.perf_counter()
total = 0
for _ in range(ITERS):
    g = astar()
    if g >= 0:
        total += g
elapsed_ms = (time.perf_counter() - t0) * 1000
us_per_run = round(elapsed_ms * 1000 / ITERS)
print(f"A* 100x100, {ITERS} runs: {elapsed_ms:.1f} ms  ({us_per_run} us/run, path={total // ITERS} steps)")
