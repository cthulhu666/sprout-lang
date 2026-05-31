// A* on a 100×100 grid — sorted-slice open set, mutable int arrays.
// Same wall rule and sorted-list open set as the Sprout implementation.
// g_score and closed use flat [10000]int / [10000]bool for O(1) access.
// Open-set insert: extend by 1 then in-place copy-shift — O(n) time, O(1) amortised alloc.
//
// Run: go run astar.go
package main

import (
	"fmt"
	"time"
)

const (
	W     = 100
	GoalX = 99
	GoalY = 99
	Inf   = 9999999
	Iters = 600
)

func isWall(x, y int) bool {
	if x <= 0 || y <= 0 || x >= GoalX || y >= GoalY {
		return false
	}
	return (x*5+y*3)%13 < 4
}

func heur(x, y int) int {
	dx := GoalX - x
	if dx < 0 {
		dx = -dx
	}
	dy := GoalY - y
	if dy < 0 {
		dy = -dy
	}
	return dx + dy
}

type entry struct{ f, g, x, y int }

// Sorted slice — O(n) insert, same asymptotic class as Sprout's sorted list.
type openSet []entry

func (o *openSet) insert(e entry) {
	*o = append(*o, entry{}) // extend by 1 (amortised; reuses backing array when cap > len)
	s := *o
	i := 0
	for i < len(s)-1 && s[i].f <= e.f {
		i++
	}
	copy(s[i+1:], s[i:len(s)-1]) // shift tail right in place
	s[i] = e
}

var dirs = [4][2]int{{0, -1}, {0, 1}, {-1, 0}, {1, 0}}

func astar() int {
	var gScore [W * W]int
	var closed [W * W]bool
	for i := range gScore {
		gScore[i] = Inf
	}
	gScore[0] = 0

	open := openSet{{heur(0, 0), 0, 0, 0}}

	for len(open) > 0 {
		cur := open[0]
		open = open[1:]
		x, y, g := cur.x, cur.y, cur.g

		if x == GoalX && y == GoalY {
			return g
		}
		idx := y*W + x
		if closed[idx] {
			continue
		}
		closed[idx] = true

		for _, d := range dirs {
			nx, ny := x+d[0], y+d[1]
			if nx < 0 || nx >= W || ny < 0 || ny >= W {
				continue
			}
			nidx := ny*W + nx
			if isWall(nx, ny) || closed[nidx] {
				continue
			}
			ng := g + 1
			if ng < gScore[nidx] {
				gScore[nidx] = ng
				open.insert(entry{ng + heur(nx, ny), ng, nx, ny})
			}
		}
	}
	return -1
}

func main() {
	// warmup
	for i := 0; i < 5; i++ {
		astar()
	}

	t := time.Now()
	total := 0
	for i := 0; i < Iters; i++ {
		g := astar()
		if g >= 0 {
			total += g
		}
	}
	elapsed := time.Since(t)
	ms := float64(elapsed.Microseconds()) / 1000.0
	usPerRun := elapsed.Microseconds() / int64(Iters)
	fmt.Printf("A* 100x100, %d runs: %.1f ms  (%d us/run, path=%d steps)\n",
		Iters, ms, usPerRun, total/Iters)
}
