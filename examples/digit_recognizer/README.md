# Handwritten digit recognizer

A 64→24→10 multilayer perceptron, written in Sprout, that **learns to recognize
handwritten digits** from real public data by backpropagation and gradient
descent. It reaches ~90% test accuracy on a held-out set (peaks at 90.0%, lands
at 89–90% — SGD oscillates by a sample or two on the 150-sample test set).

`recognizer.sprout` is a single-module program (Sprout does not yet support
multi-module user programs outside `stdlib/`); the dataset ships alongside it as
two whitespace-separated text files.

## Running

From the repository root:

```sh
./build/compile_driver_bin_stage1 --emit-ir "$PWD/stdlib" \
    examples/digit_recognizer/recognizer.sprout > /tmp/digits.ll
clang /tmp/digits.ll runtime/sprout_runtime.c -O2 \
    -framework Security -framework CoreFoundation -o /tmp/digits   # frameworks: macOS only
/tmp/digits
```

Expected output (deterministic — fixed seed): test accuracy climbs from ~9%
(chance) to ~90% over 25 epochs. It reads the data files by a path relative to
the repository root, so run it from there.

## Model

- **Input:** 64 pixels (8×8 image), each an integer 0–16, normalized to [0, 1].
- **Hidden:** 24 units, **softsign** activation `s(x) = x/(1+|x|)` — smooth and
  bounded like tanh but built from pure arithmetic, so no `exp` is required.
- **Output:** 10 linear units, trained toward a one-hot target under mean
  squared error; the prediction is `argmax`. (No softmax → no `exp`.)
- **Training:** stochastic gradient descent, one weight update per sample.

## Dataset

The data is a bounded subset (first 500 training / 150 test samples) of the
**Optical Recognition of Handwritten Digits** data set — the 8×8 cousin of MNIST.

- Source: UCI Machine Learning Repository —
  <https://archive.ics.uci.edu/dataset/80/optical+recognition+of+handwritten+digits>
- Citation: Alpaydin, E. & Kaynak, C. (1998). *Optical Recognition of
  Handwritten Digits*. UCI Machine Learning Repository.
- License: **CC BY 4.0** (<https://creativecommons.org/licenses/by/4.0/>).

`optdigits_train.txt` and `optdigits_test.txt` are the original CSV rows with
commas replaced by spaces and truncated to the subset size; the 65 values per
line are 64 pixels followed by the label.
