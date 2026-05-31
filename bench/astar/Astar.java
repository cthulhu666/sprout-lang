import java.util.PriorityQueue;
import java.util.Arrays;
import java.util.Locale;

/**
 * A* on a 100×100 grid — PriorityQueue open set (O(log n)), mutable int[].
 * Uses the same wall rule as the Sprout implementation.
 * g_score and closed use flat int[]/boolean[] indexed by y*W+x for O(1) access.
 * PriorityQueue gives O(log n) offer/poll (vs Sprout's O(n) sorted-list insert).
 */
public class Astar {
    static final int W      = 100;
    static final int GOAL_X = 99;
    static final int GOAL_Y = 99;
    static final int INF    = 9_999_999;
    static final int ITERS  = 9000;

    static final int[][] DIRS = {{0,-1},{0,1},{-1,0},{1,0}};

    static boolean isWall(int x, int y) {
        if (x <= 0 || y <= 0 || x >= GOAL_X || y >= GOAL_Y) return false;
        return (x * 5 + y * 3) % 13 < 4;
    }

    static int h(int x, int y) {
        return Math.abs(GOAL_X - x) + Math.abs(GOAL_Y - y);
    }

    static int astar() {
        int[]     gScore = new int[W * W];
        boolean[] closed = new boolean[W * W];
        Arrays.fill(gScore, INF);
        gScore[0] = 0;

        // {f, g, x, y} — ordered by f ascending
        PriorityQueue<int[]> open = new PriorityQueue<>((a, b) -> a[0] - b[0]);
        open.offer(new int[]{h(0, 0), 0, 0, 0});

        while (!open.isEmpty()) {
            int[] cur = open.poll();
            int g = cur[1], x = cur[2], y = cur[3];

            if (x == GOAL_X && y == GOAL_Y) return g;
            int idx = y * W + x;
            if (closed[idx]) continue;
            closed[idx] = true;

            for (int[] d : DIRS) {
                int nx = x + d[0], ny = y + d[1];
                if (nx < 0 || nx >= W || ny < 0 || ny >= W) continue;
                int nidx = ny * W + nx;
                if (isWall(nx, ny) || closed[nidx]) continue;
                int ng = g + 1;
                if (ng < gScore[nidx]) {
                    gScore[nidx] = ng;
                    open.offer(new int[]{ng + h(nx, ny), ng, nx, ny});
                }
            }
        }
        return -1;
    }

    public static void main(String[] args) {
        // warmup — let the JIT compile the hot path
        for (int i = 0; i < 10; i++) astar();

        long t0 = System.nanoTime();
        int total = 0;
        for (int i = 0; i < ITERS; i++) {
            int g = astar();
            if (g >= 0) total += g;
        }
        long elapsed = System.nanoTime() - t0;
        double ms = elapsed / 1_000_000.0;
        long usPerRun = elapsed / 1000 / ITERS;
        System.out.printf(Locale.US, "A* 100x100, %d runs: %.1f ms  (%d us/run, path=%d steps)%n",
            ITERS, ms, usPerRun, total / ITERS);
    }
}
