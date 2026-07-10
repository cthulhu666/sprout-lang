import scala.io.Source

// 64 -> 24 -> 10 softsign MLP, MSE, SGD — the SAME algorithm/seed as Recognizer.scala,
// written in idiomatic functional Scala: immutable Vector weights, a case-class Net threaded
// through foldLeft, zip/map/fold instead of index loops, no var in the core. Accumulation
// order is preserved (fold seeded with the bias), so it reaches the identical 89.33%.
// This exists to measure the cost of FP purity + boxed collections against Recognizer.scala.
object RecognizerIdiomatic:
  final val NIn = 64
  final val NHid = 24
  final val NOut = 10

  def dabs(x: Double): Double = if x < 0 then -x else x
  def softsign(x: Double): Double = x / (1.0 + dabs(x))
  def softsignDeriv(s: Double): Double = { val d = 1.0 - dabs(s); d * d }
  def lcgNext(s: Long): Long = (1664525L * s + 1013904223L) % 2147483648L
  def lcgWeight(s: Long): Double = (s % 2000 - 1000).toDouble / 10000.0
  def target(o: Int, label: Int): Double = if o == label then 1.0 else 0.0

  final case class Sample(pixels: Vector[Double], label: Int)

  final case class Net(
      w1: Vector[Vector[Double]], // NHid x NIn
      b1: Vector[Double],         // NHid
      w2: Vector[Vector[Double]], // NOut x NHid
      b2: Vector[Double]          // NOut
  )

  // One LCG sequence threaded through W1 (row-major), B1, W2 (row-major), B2 — same
  // order as the imperative port, so weight k consumes state s_k.
  def initNet: Net =
    val nW1 = NHid * NIn
    val nB1 = NHid
    val nW2 = NOut * NHid
    val total = nW1 + nB1 + nW2 + NOut
    val flat = LazyList.iterate(12345L)(lcgNext).map(lcgWeight).take(total).toVector
    Net(
      w1 = flat.slice(0, nW1).grouped(NIn).toVector,
      b1 = flat.slice(nW1, nW1 + nB1),
      w2 = flat.slice(nW1 + nB1, nW1 + nB1 + nW2).grouped(NHid).toVector,
      b2 = flat.slice(nW1 + nB1 + nW2, total)
    )

  // Dot product seeded with the bias — reproduces the imperative `z = bias; z += w*x` order.
  def dotBias(row: Vector[Double], v: Vector[Double], bias: Double): Double =
    row.zip(v).foldLeft(bias) { case (z, (w, x)) => z + w * x }

  def forward(net: Net, x: Vector[Double]): (Vector[Double], Vector[Double]) =
    val hidden = net.w1.zip(net.b1).map { case (row, bias) => softsign(dotBias(row, x, bias)) }
    val out = net.w2.zip(net.b2).map { case (row, bias) => dotBias(row, hidden, bias) }
    (hidden, out)

  def trainOne(net: Net, sample: Sample, lr: Double): Net =
    val (hidden, out) = forward(net, sample.pixels)
    val label = sample.label
    val douts = out.indices.toVector.map(o => 2.0 * (out(o) - target(o, label)))

    // dhidden(h) = Σ_o dout(o) * w2(o)(h), accumulated in o order from 0.0.
    val dhidden = (0 until NHid).toVector.map { h =>
      net.w2.zip(douts).foldLeft(0.0) { case (acc, (row, dout)) => acc + dout * row(h) }
    }
    val newW2 = net.w2.zip(douts).map { case (row, dout) =>
      row.zip(hidden).map { case (w, hid) => w - lr * dout * hid }
    }
    val newB2 = net.b2.zip(douts).map { case (b, dout) => b - lr * dout }

    val dz = hidden.zip(dhidden).map { case (hid, dh) => dh * softsignDeriv(hid) }
    val newW1 = net.w1.zip(dz).map { case (row, d) =>
      row.zip(sample.pixels).map { case (w, xi) => w - lr * d * xi }
    }
    val newB1 = net.b1.zip(dz).map { case (b, d) => b - lr * d }

    Net(newW1, newB1, newW2, newB2)

  def argmax(out: Vector[Double]): Int =
    out.indices.foldLeft(0)((best, o) => if out(o) > out(best) then o else best)

  def loadDataset(path: String): Vector[Sample] =
    Source.fromFile(path).getLines().flatMap { line =>
      val ints = line.trim.split("\\s+").filter(_.nonEmpty).flatMap(_.toIntOption)
      Option.when(ints.length >= NIn + 1) {
        Sample(ints.take(NIn).map(_ / 16.0).toVector, ints(NIn))
      }
    }.toVector

  def accuracy(net: Net, samples: Vector[Sample]): Double =
    val correct = samples.count(s => argmax(forward(net, s.pixels)._2) == s.label)
    correct * 100.0 / samples.length

  def main(args: Array[String]): Unit =
    val train = loadDataset("examples/digit_recognizer/optdigits_train.txt")
    val test = loadDataset("examples/digit_recognizer/optdigits_test.txt")
    printf("loaded %d train / %d test samples%n", train.length, test.length)

    val lr = 0.05
    val trained = (0 to 25).foldLeft(initNet) { (net, epoch) =>
      if epoch % 5 == 0 then
        printf("epoch %d  test accuracy %s%%%n", epoch, accuracy(net, test))
      train.foldLeft(net)((n, s) => trainOne(n, s, lr))
    }
    printf("final test accuracy: %s%%%n", accuracy(trained, test))
