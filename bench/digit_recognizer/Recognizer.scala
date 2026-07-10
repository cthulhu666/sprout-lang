import scala.io.Source

// 64 -> 24 -> 10 softsign MLP, MSE, SGD; same algorithm/seed as the Sprout + Java versions.
// Imperative port: mutable Array[Double] + while loops, matching Java's double[][] for a
// fair execution-cost comparison. See RecognizerIdiomatic.scala for the functional variant.
object Recognizer:
  final val NIn = 64
  final val NHid = 256
  final val NOut = 10

  inline def dabs(x: Double): Double = if x < 0 then -x else x
  inline def softsign(x: Double): Double = x / (1.0 + dabs(x))
  inline def softsignDeriv(s: Double): Double = { val d = 1.0 - dabs(s); d * d }
  def lcgNext(s: Long): Long = (1664525L * s + 1013904223L) % 2147483648L
  def lcgWeight(s: Long): Double = (s % 2000 - 1000).toDouble / 10000.0
  inline def target(o: Int, label: Int): Double = if o == label then 1.0 else 0.0

  final case class Sample(pixels: Array[Double], label: Int)

  // W1 (NHid x NIn), B1, W2 (NOut x NHid), B2 — one LCG sequence threaded through all.
  final class Net:
    val w1 = Array.ofDim[Double](NHid, NIn)
    val b1 = new Array[Double](NHid)
    val w2 = Array.ofDim[Double](NOut, NHid)
    val b2 = new Array[Double](NOut)

    locally:
      var s = 12345L
      var h = 0
      while h < NHid do
        var i = 0
        while i < NIn do { w1(h)(i) = lcgWeight(s); s = lcgNext(s); i += 1 }
        h += 1
      h = 0
      while h < NHid do { b1(h) = lcgWeight(s); s = lcgNext(s); h += 1 }
      var o = 0
      while o < NOut do
        var hh = 0
        while hh < NHid do { w2(o)(hh) = lcgWeight(s); s = lcgNext(s); hh += 1 }
        o += 1
      o = 0
      while o < NOut do { b2(o) = lcgWeight(s); s = lcgNext(s); o += 1 }

    def forward(x: Array[Double], hidden: Array[Double], out: Array[Double]): Unit =
      var h = 0
      while h < NHid do
        var z = b1(h)
        var i = 0
        while i < NIn do { z += w1(h)(i) * x(i); i += 1 }
        hidden(h) = softsign(z)
        h += 1
      var o = 0
      while o < NOut do
        var z = b2(o)
        var hh = 0
        while hh < NHid do { z += w2(o)(hh) * hidden(hh); hh += 1 }
        out(o) = z
        o += 1

    def backprop(x: Array[Double], label: Int, hidden: Array[Double], out: Array[Double],
                 dhidden: Array[Double], lr: Double): Unit =
      java.util.Arrays.fill(dhidden, 0.0)
      var o = 0
      while o < NOut do
        val dout = 2.0 * (out(o) - target(o, label))
        var h = 0
        while h < NHid do { dhidden(h) += dout * w2(o)(h); h += 1 }
        o += 1
      o = 0
      while o < NOut do
        val dout = 2.0 * (out(o) - target(o, label))
        var h = 0
        while h < NHid do { w2(o)(h) -= lr * dout * hidden(h); h += 1 }
        b2(o) -= lr * dout
        o += 1
      var h = 0
      while h < NHid do
        val dz = dhidden(h) * softsignDeriv(hidden(h))
        var i = 0
        while i < NIn do { w1(h)(i) -= lr * dz * x(i); i += 1 }
        b1(h) -= lr * dz
        h += 1

  def argmax(out: Array[Double]): Int =
    var best = 0
    var bv = out(0)
    var o = 1
    while o < NOut do { if out(o) > bv then { best = o; bv = out(o) }; o += 1 }
    best

  def loadDataset(path: String): Array[Sample] =
    val samples = scala.collection.mutable.ArrayBuffer.empty[Sample]
    for line <- Source.fromFile(path).getLines() do
      val fields = line.trim.split("\\s+").filter(_.nonEmpty)
      val ints = fields.flatMap(t => t.toIntOption)
      if ints.length >= NIn + 1 then
        val px = new Array[Double](NIn)
        var i = 0
        while i < NIn do { px(i) = ints(i) / 16.0; i += 1 }
        samples += Sample(px, ints(NIn))
    samples.toArray

  def accuracy(net: Net, samples: Array[Sample], hidden: Array[Double], out: Array[Double]): Double =
    var correct = 0
    var k = 0
    while k < samples.length do
      net.forward(samples(k).pixels, hidden, out)
      if argmax(out) == samples(k).label then correct += 1
      k += 1
    correct * 100.0 / samples.length

  def main(args: Array[String]): Unit =
    val train = loadDataset("examples/digit_recognizer/optdigits_train.txt")
    val test = loadDataset("examples/digit_recognizer/optdigits_test.txt")
    printf("loaded %d train / %d test samples%n", train.length, test.length)

    val net = new Net
    val hidden = new Array[Double](NHid)
    val out = new Array[Double](NOut)
    val dhidden = new Array[Double](NHid)
    val lr = 0.05
    var epoch = 0
    while epoch <= 25 do
      if epoch % 5 == 0 then
        printf("epoch %d  test accuracy %s%%%n", epoch, accuracy(net, test, hidden, out))
      var k = 0
      while k < train.length do
        net.forward(train(k).pixels, hidden, out)
        net.backprop(train(k).pixels, train(k).label, hidden, out, dhidden, lr)
        k += 1
      epoch += 1
    printf("final test accuracy: %s%%%n", accuracy(net, test, hidden, out))
