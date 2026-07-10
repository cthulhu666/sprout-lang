package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const (
	nIn  = 64
	nHid = 256
	nOut = 10
)

// Net: W1 (nHid x nIn), B1 (nHid), W2 (nOut x nHid), B2 (nOut).
type Net struct {
	w1 [][]float64
	b1 []float64
	w2 [][]float64
	b2 []float64
}

type Sample struct {
	pixels []float64
	label  int
}

func matrix(rows, cols int) [][]float64 {
	m := make([][]float64, rows)
	for r := range m {
		m[r] = make([]float64, cols)
	}
	return m
}

func dabs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
func softsign(x float64) float64      { return x / (1.0 + dabs(x)) }
func softsignDeriv(s float64) float64 { d := 1.0 - dabs(s); return d * d }

func modInt(a, m int) int      { return a - (a/m)*m }
func lcgNext(s int) int        { return modInt(1664525*s+1013904223, 2147483648) }
func lcgWeight(s int) float64  { return float64(modInt(s, 2000)-1000) / 10000.0 }
func target(o, label int) float64 {
	if o == label {
		return 1.0
	}
	return 0.0
}

// newNet threads one LCG sequence through W1 (row-major), B1, W2, B2.
func newNet() *Net {
	n := &Net{matrix(nHid, nIn), make([]float64, nHid), matrix(nOut, nHid), make([]float64, nOut)}
	s := 12345
	for h := 0; h < nHid; h++ {
		for i := 0; i < nIn; i++ {
			n.w1[h][i] = lcgWeight(s)
			s = lcgNext(s)
		}
	}
	for h := 0; h < nHid; h++ {
		n.b1[h] = lcgWeight(s)
		s = lcgNext(s)
	}
	for o := 0; o < nOut; o++ {
		for h := 0; h < nHid; h++ {
			n.w2[o][h] = lcgWeight(s)
			s = lcgNext(s)
		}
	}
	for o := 0; o < nOut; o++ {
		n.b2[o] = lcgWeight(s)
		s = lcgNext(s)
	}
	return n
}

func (n *Net) forward(x, hidden, out []float64) {
	for h := 0; h < nHid; h++ {
		z := n.b1[h]
		for i := 0; i < nIn; i++ {
			z += n.w1[h][i] * x[i]
		}
		hidden[h] = softsign(z)
	}
	for o := 0; o < nOut; o++ {
		z := n.b2[o]
		for h := 0; h < nHid; h++ {
			z += n.w2[o][h] * hidden[h]
		}
		out[o] = z
	}
}

func (n *Net) backprop(x []float64, label int, hidden, out, dhidden []float64, lr float64) {
	for h := 0; h < nHid; h++ {
		dhidden[h] = 0.0
	}
	for o := 0; o < nOut; o++ {
		dout := 2.0 * (out[o] - target(o, label))
		for h := 0; h < nHid; h++ {
			dhidden[h] += dout * n.w2[o][h]
		}
	}
	for o := 0; o < nOut; o++ {
		dout := 2.0 * (out[o] - target(o, label))
		for h := 0; h < nHid; h++ {
			n.w2[o][h] -= lr * dout * hidden[h]
		}
		n.b2[o] -= lr * dout
	}
	for h := 0; h < nHid; h++ {
		dz := dhidden[h] * softsignDeriv(hidden[h])
		for i := 0; i < nIn; i++ {
			n.w1[h][i] -= lr * dz * x[i]
		}
		n.b1[h] -= lr * dz
	}
}

func argmax(out []float64) int {
	best, bv := 0, out[0]
	for o := 1; o < nOut; o++ {
		if out[o] > bv {
			best, bv = o, out[o]
		}
	}
	return best
}

func loadDataset(path string) []Sample {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var samples []Sample
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		ints := make([]int, 0, len(fields))
		for _, tok := range fields {
			if v, err := strconv.Atoi(tok); err == nil {
				ints = append(ints, v)
			}
		}
		if len(ints) < nIn+1 {
			continue
		}
		px := make([]float64, nIn)
		for i := 0; i < nIn; i++ {
			px[i] = float64(ints[i]) / 16.0
		}
		samples = append(samples, Sample{px, ints[nIn]})
	}
	return samples
}

func (n *Net) accuracy(samples []Sample, hidden, out []float64) float64 {
	correct := 0
	for _, s := range samples {
		n.forward(s.pixels, hidden, out)
		if argmax(out) == s.label {
			correct++
		}
	}
	return float64(correct) * 100.0 / float64(len(samples))
}

func main() {
	train := loadDataset("examples/digit_recognizer/optdigits_train.txt")
	test := loadDataset("examples/digit_recognizer/optdigits_test.txt")
	fmt.Printf("loaded %d train / %d test samples\n", len(train), len(test))

	net := newNet()
	hidden := make([]float64, nHid)
	out := make([]float64, nOut)
	dhidden := make([]float64, nHid)
	lr := 0.05

	for epoch := 0; epoch <= 25; epoch++ {
		if epoch%5 == 0 {
			fmt.Printf("epoch %d  test accuracy %g%%\n", epoch, net.accuracy(test, hidden, out))
		}
		for _, s := range train {
			net.forward(s.pixels, hidden, out)
			net.backprop(s.pixels, s.label, hidden, out, dhidden, lr)
		}
	}
	fmt.Printf("final test accuracy: %g%%\n", net.accuracy(test, hidden, out))
}
