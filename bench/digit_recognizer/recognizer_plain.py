"""Handwritten-digit recognition — plain implementation, no ML libraries.
64 -> 24 -> 10 softsign MLP, MSE, SGD; same algorithm as the Sprout version."""

N_IN, N_HID, N_OUT = 64, 256, 10


def dabs(x):
    return -x if x < 0 else x


def softsign(x):
    return x / (1.0 + dabs(x))


def softsign_deriv(s):
    d = 1.0 - dabs(s)
    return d * d


def mod_int(a, m):
    return a - (a // m) * m


def lcg_next(s):
    return mod_int(1664525 * s + 1013904223, 2147483648)


def lcg_weight(s):
    return (mod_int(s, 2000) - 1000) / 10000.0


class Net:
    # W1 (N_HID x N_IN), B1, W2 (N_OUT x N_HID), B2 — one LCG sequence, row-major.
    def __init__(self):
        self.w1 = [[0.0] * N_IN for _ in range(N_HID)]
        self.b1 = [0.0] * N_HID
        self.w2 = [[0.0] * N_HID for _ in range(N_OUT)]
        self.b2 = [0.0] * N_OUT
        s = 12345
        for h in range(N_HID):
            for i in range(N_IN):
                self.w1[h][i] = lcg_weight(s)
                s = lcg_next(s)
        for h in range(N_HID):
            self.b1[h] = lcg_weight(s)
            s = lcg_next(s)
        for o in range(N_OUT):
            for h in range(N_HID):
                self.w2[o][h] = lcg_weight(s)
                s = lcg_next(s)
        for o in range(N_OUT):
            self.b2[o] = lcg_weight(s)
            s = lcg_next(s)

    def forward(self, x):
        hidden = [softsign(self.b1[h] + sum(self.w1[h][i] * x[i] for i in range(N_IN)))
                  for h in range(N_HID)]
        out = [self.b2[o] + sum(self.w2[o][h] * hidden[h] for h in range(N_HID))
               for o in range(N_OUT)]
        return hidden, out

    def backprop(self, x, label, hidden, out, lr):
        dhidden = [0.0] * N_HID
        for o in range(N_OUT):
            dout = 2.0 * (out[o] - (1.0 if o == label else 0.0))
            for h in range(N_HID):
                dhidden[h] += dout * self.w2[o][h]
        for o in range(N_OUT):
            dout = 2.0 * (out[o] - (1.0 if o == label else 0.0))
            for h in range(N_HID):
                self.w2[o][h] -= lr * dout * hidden[h]
            self.b2[o] -= lr * dout
        for h in range(N_HID):
            dz = dhidden[h] * softsign_deriv(hidden[h])
            for i in range(N_IN):
                self.w1[h][i] -= lr * dz * x[i]
            self.b1[h] -= lr * dz


def load_dataset(path):
    samples = []
    try:
        with open(path) as f:
            for line in f:
                ints = [int(t) for t in line.split() if t.lstrip("-").isdigit()]
                if len(ints) < N_IN + 1:
                    continue
                pixels = [ints[i] / 16.0 for i in range(N_IN)]
                samples.append((pixels, ints[N_IN]))
    except OSError:
        pass
    return samples


def accuracy(net, samples):
    correct = 0
    for px, label in samples:
        _, out = net.forward(px)
        if max(range(N_OUT), key=lambda o: out[o]) == label:
            correct += 1
    return correct * 100.0 / len(samples)


def main():
    train = load_dataset("examples/digit_recognizer/optdigits_train.txt")
    test = load_dataset("examples/digit_recognizer/optdigits_test.txt")
    print(f"loaded {len(train)} train / {len(test)} test samples")

    net = Net()
    lr = 0.05
    for epoch in range(26):
        if epoch % 5 == 0:
            print(f"epoch {epoch}  test accuracy {accuracy(net, test)}%")
        for px, label in train:
            hidden, out = net.forward(px)
            net.backprop(px, label, hidden, out, lr)
    print(f"final test accuracy: {accuracy(net, test)}%")


if __name__ == "__main__":
    main()
