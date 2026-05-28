# N-Queens: mutable backtracking — write true, recurse, write false.
# skip is computed BEFORE mutation so it sees unmodified arrays.
# Run: ruby nqueens_mut.rb

def queens(n, row, col, cols, pos_diag, neg_diag)
  return 1 if row == n
  return 0 if col >= n

  pd = row + col
  nd = row - col + n - 1

  skip = queens(n, row, col + 1, cols, pos_diag, neg_diag)

  if !cols[col] && !pos_diag[pd] && !neg_diag[nd]
    cols[col] = true; pos_diag[pd] = true; neg_diag[nd] = true
    place = queens(n, row + 1, 0, cols, pos_diag, neg_diag)
    cols[col] = false; pos_diag[pd] = false; neg_diag[nd] = false
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
