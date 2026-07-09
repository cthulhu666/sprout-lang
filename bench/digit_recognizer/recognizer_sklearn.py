"""Handwritten-digit recognition with a library (scikit-learn).
The library owns the model, training loop, and optimiser — so the whole program
is load + fit + score. (Not the identical hand-SGD algorithm: scikit-learn has no
softsign, so this uses its closest bounded activation, tanh. Same *task*.)

Run: pip install numpy scikit-learn, then `python recognizer_sklearn.py`."""

import numpy as np
from sklearn.neural_network import MLPClassifier


def load(path):
    data = np.loadtxt(path)
    return data[:, :64] / 16.0, data[:, 64].astype(int)


x_train, y_train = load("examples/digit_recognizer/optdigits_train.txt")
x_test, y_test = load("examples/digit_recognizer/optdigits_test.txt")
print(f"loaded {len(x_train)} train / {len(x_test)} test samples")

clf = MLPClassifier(
    hidden_layer_sizes=(24,),
    activation="tanh",
    solver="sgd",
    learning_rate_init=0.05,
    max_iter=25,
    random_state=0,
)
clf.fit(x_train, y_train)
print(f"final test accuracy: {clf.score(x_test, y_test) * 100:.4f}%")
