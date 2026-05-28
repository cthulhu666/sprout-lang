# N-Queens: pure immutable approach — Array#dup per placement (same as Sprout).
# Run: ruby nqueens_pure.rb

def queens(n, row, col, cols, pos_diag, neg_diag)
  return 1 if row == n
  return 0 if col >= n

  skip = queens(n, row, col + 1, cols, pos_diag, neg_diag)

  pd = row + col
  nd = row - col + n - 1

  if !cols[col] && !pos_diag[pd] && !neg_diag[nd]
    c2 = cols.dup;     c2[col] = true
    p2 = pos_diag.dup; p2[pd]  = true
    n2 = neg_diag.dup; n2[nd]  = true
    place = queens(n, row + 1, 0, c2, p2, n2)
  else
    place = 0
  end

  skip + place
end

def count_solutions(n)
  queens(n, 0, 0,
    Array.new(n,       false),
    Array.new(2*n - 1, false),
    Array.new(2*n - 1, false))
end

[1, 4, 8, 10, 12, 13].each do |n|
  t = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  c = count_solutions(n)
  ms = (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t) * 1000
  printf "N=%d: %d  (%.1f ms)\n", n, c, ms
end
