"use strict";
// A* on a 100×100 grid — sorted-array open set (O(n) insert), typed arrays.
// Uses the same wall rule as the Sprout implementation.
// g_score: Int32Array, closed: Uint8Array — both indexed by y*W+x for O(1) access.
// Sorted insertion matches Sprout's sorted-list open set in asymptotic complexity.

const W      = 100;
const GOAL_X = 99;
const GOAL_Y = 99;
const INF    = 9_999_999;
const ITERS  = 1300;
const DIRS   = [[0,-1],[0,1],[-1,0],[1,0]];

function isWall(x, y) {
    if (x <= 0 || y <= 0 || x >= GOAL_X || y >= GOAL_Y) return false;
    return (x * 5 + y * 3) % 13 < 4;
}

function h(x, y) {
    return Math.abs(GOAL_X - x) + Math.abs(GOAL_Y - y);
}

// Insert [f, g, x, y] into open array keeping it sorted by f ascending.
function openInsert(open, f, g, x, y) {
    let lo = 0, hi = open.length;
    while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (open[mid][0] <= f) lo = mid + 1;
        else hi = mid;
    }
    open.splice(lo, 0, [f, g, x, y]);
}

function astar() {
    const gScore = new Int32Array(W * W).fill(INF);
    const closed = new Uint8Array(W * W);
    gScore[0] = 0;

    const open = [[h(0, 0), 0, 0, 0]];

    while (open.length > 0) {
        const [, g, x, y] = open.shift();
        if (x === GOAL_X && y === GOAL_Y) return g;
        const idx = y * W + x;
        if (closed[idx]) continue;
        closed[idx] = 1;

        for (const [dx, dy] of DIRS) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || nx >= W || ny < 0 || ny >= W) continue;
            const nidx = ny * W + nx;
            if (isWall(nx, ny) || closed[nidx]) continue;
            const ng = g + 1;
            if (ng < gScore[nidx]) {
                gScore[nidx] = ng;
                openInsert(open, ng + h(nx, ny), ng, nx, ny);
            }
        }
    }
    return -1;
}

// warmup — let V8 JIT compile the hot path
for (let i = 0; i < 10; i++) astar();

const t0 = process.hrtime.bigint();
let total = 0;
for (let i = 0; i < ITERS; i++) {
    const g = astar();
    if (g >= 0) total += g;
}
const elapsedNs = process.hrtime.bigint() - t0;
const elapsedMs = Number(elapsedNs) / 1_000_000;
const usPerRun = Math.round(Number(elapsedNs) / 1000 / ITERS);
console.log(`A* 100x100, ${ITERS} runs: ${elapsedMs.toFixed(1)} ms  (${usPerRun} us/run, path=${Math.floor(total / ITERS)} steps)`);
