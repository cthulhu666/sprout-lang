// N-Queens in Go: three variants — pure (slice copy), mutable (backtracking), bitmask.
// Run: go run nqueens.go
package main

import (
	"fmt"
	"time"
)

// ── Pure: slice copy on every placement (same algorithm as Sprout) ─────────────

func copySlice(s []bool) []bool {
	c := make([]bool, len(s))
	copy(c, s)
	return c
}

func queensPure(n, row, col int, cols, posDiag, negDiag []bool) int {
	if row == n {
		return 1
	}
	if col >= n {
		return 0
	}
	skip := queensPure(n, row, col+1, cols, posDiag, negDiag)
	pd := row + col
	nd := row - col + n - 1
	place := 0
	if !cols[col] && !posDiag[pd] && !negDiag[nd] {
		c2 := copySlice(cols); c2[col] = true
		p2 := copySlice(posDiag); p2[pd] = true
		n2 := copySlice(negDiag); n2[nd] = true
		place = queensPure(n, row+1, 0, c2, p2, n2)
	}
	return skip + place
}

func countPure(n int) int {
	return queensPure(n, 0, 0, make([]bool, n), make([]bool, 2*n-1), make([]bool, 2*n-1))
}

// ── Mutable: backtracking undo — zero allocation per step ─────────────────────

func queensMut(n, row, col int, cols, posDiag, negDiag []bool) int {
	if row == n {
		return 1
	}
	if col >= n {
		return 0
	}
	pd := row + col
	nd := row - col + n - 1
	skip := queensMut(n, row, col+1, cols, posDiag, negDiag)
	place := 0
	if !cols[col] && !posDiag[pd] && !negDiag[nd] {
		cols[col] = true; posDiag[pd] = true; negDiag[nd] = true
		place = queensMut(n, row+1, 0, cols, posDiag, negDiag)
		cols[col] = false; posDiag[pd] = false; negDiag[nd] = false
	}
	return skip + place
}

func countMut(n int) int {
	return queensMut(n, 0, 0, make([]bool, n), make([]bool, 2*n-1), make([]bool, 2*n-1))
}

// ── Bitmask: three ints, no arrays — O(1) per step, iterates only safe cols ──
// Uses the Richards encoding: cols = occupied columns (permanent);
// ld/rd = diagonals projected onto the current row, shifted left/right each level.

func queensBitmask(n, cols, ld, rd int) int {
	mask := (1 << n) - 1
	if cols == mask {
		return 1
	}
	available := mask &^ (cols | ld | rd)
	count := 0
	for available != 0 {
		bit := available & -available // isolate lowest set bit
		available &= available - 1   // clear it
		count += queensBitmask(n, cols|bit, (ld|bit)<<1, (rd|bit)>>1)
	}
	return count
}

func countBitmask(n int) int {
	return queensBitmask(n, 0, 0, 0)
}

// ── Driver ────────────────────────────────────────────────────────────────────

func bench(label string, f func(int) int, ns []int) {
	for _, n := range ns {
		t := time.Now()
		c := f(n)
		ms := float64(time.Since(t).Microseconds()) / 1000.0
		fmt.Printf("[%-7s] N=%d: %d  (%.2f ms)\n", label, n, c, ms)
	}
}

func main() {
	ns := []int{1, 4, 8, 10, 12, 13}
	bench("pure", countPure, ns)
	bench("mutable", countMut, ns)
	bench("bitmask", countBitmask, ns)
}
