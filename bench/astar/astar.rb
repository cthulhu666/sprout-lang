# A* on a 100×100 grid — bsearch_index sorted-array open set, mutable arrays.
# Uses the same wall rule as the Sprout implementation.
# g_score and closed use flat arrays indexed by y*W+x for O(1) access.

W      = 100
GOAL_X = 99
GOAL_Y = 99
INF    = 9_999_999
ITERS  = 120
DIRS   = [[0, -1], [0, 1], [-1, 0], [1, 0]].freeze

def is_wall(x, y)
  return false if x <= 0 || y <= 0 || x >= GOAL_X || y >= GOAL_Y
  (x * 5 + y * 3) % 13 < 4
end

def h(x, y)
  (GOAL_X - x).abs + (GOAL_Y - y).abs
end

def open_insert(open, f, g, x, y)
  i = open.bsearch_index { |e| e[0] > f } || open.length
  open.insert(i, [f, g, x, y])
end

def astar
  g_score = Array.new(W * W, INF)
  closed  = Array.new(W * W, false)
  g_score[0] = 0
  open = [[h(0, 0), 0, 0, 0]]

  until open.empty?
    _, g, x, y = open.shift
    return g if x == GOAL_X && y == GOAL_Y
    idx = y * W + x
    next if closed[idx]
    closed[idx] = true

    DIRS.each do |dx, dy|
      nx, ny = x + dx, y + dy
      next unless (0...W).cover?(nx) && (0...W).cover?(ny)
      nidx = ny * W + nx
      next if is_wall(nx, ny) || closed[nidx]
      ng = g + 1
      if ng < g_score[nidx]
        g_score[nidx] = ng
        open_insert(open, ng + h(nx, ny), ng, nx, ny)
      end
    end
  end
  -1
end

# warmup
5.times { astar }

t0    = Process.clock_gettime(Process::CLOCK_MONOTONIC)
total = 0
ITERS.times do
  g = astar
  total += g if g >= 0
end
elapsed_ms = (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0) * 1000
us_per_run = (elapsed_ms * 1000 / ITERS).round
puts format("A* 100x100, %d runs: %.1f ms  (%d us/run, path=%d steps)", ITERS, elapsed_ms, us_per_run, total / ITERS)
