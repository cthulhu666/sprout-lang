import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

// 64 -> 24 -> 10 softsign MLP, MSE, SGD; same algorithm as the Sprout version.
public class Recognizer {
    static final int N_IN = 64, N_HID = 24, N_OUT = 10;

    static double dabs(double x) { return x < 0 ? -x : x; }
    static double softsign(double x) { return x / (1.0 + dabs(x)); }
    static double softsignDeriv(double s) { double d = 1.0 - dabs(s); return d * d; }
    static long lcgNext(long s) { return (1664525L * s + 1013904223L) % 2147483648L; }
    static double lcgWeight(long s) { return (double) (s % 2000 - 1000) / 10000.0; }
    static double target(int o, int label) { return o == label ? 1.0 : 0.0; }

    // W1 (N_HID x N_IN), B1, W2 (N_OUT x N_HID), B2.
    final double[][] w1 = new double[N_HID][N_IN];
    final double[] b1 = new double[N_HID];
    final double[][] w2 = new double[N_OUT][N_HID];
    final double[] b2 = new double[N_OUT];

    record Sample(double[] pixels, int label) {}

    // One LCG sequence threaded through W1 (row-major), B1, W2, B2.
    Recognizer() {
        long s = 12345;
        for (int h = 0; h < N_HID; h++)
            for (int i = 0; i < N_IN; i++) { w1[h][i] = lcgWeight(s); s = lcgNext(s); }
        for (int h = 0; h < N_HID; h++) { b1[h] = lcgWeight(s); s = lcgNext(s); }
        for (int o = 0; o < N_OUT; o++)
            for (int h = 0; h < N_HID; h++) { w2[o][h] = lcgWeight(s); s = lcgNext(s); }
        for (int o = 0; o < N_OUT; o++) { b2[o] = lcgWeight(s); s = lcgNext(s); }
    }

    void forward(double[] x, double[] hidden, double[] out) {
        for (int h = 0; h < N_HID; h++) {
            double z = b1[h];
            for (int i = 0; i < N_IN; i++) z += w1[h][i] * x[i];
            hidden[h] = softsign(z);
        }
        for (int o = 0; o < N_OUT; o++) {
            double z = b2[o];
            for (int h = 0; h < N_HID; h++) z += w2[o][h] * hidden[h];
            out[o] = z;
        }
    }

    void backprop(double[] x, int label, double[] hidden, double[] out, double[] dhidden, double lr) {
        Arrays.fill(dhidden, 0.0);
        for (int o = 0; o < N_OUT; o++) {
            double dout = 2.0 * (out[o] - target(o, label));
            for (int h = 0; h < N_HID; h++) dhidden[h] += dout * w2[o][h];
        }
        for (int o = 0; o < N_OUT; o++) {
            double dout = 2.0 * (out[o] - target(o, label));
            for (int h = 0; h < N_HID; h++) w2[o][h] -= lr * dout * hidden[h];
            b2[o] -= lr * dout;
        }
        for (int h = 0; h < N_HID; h++) {
            double dz = dhidden[h] * softsignDeriv(hidden[h]);
            for (int i = 0; i < N_IN; i++) w1[h][i] -= lr * dz * x[i];
            b1[h] -= lr * dz;
        }
    }

    static int argmax(double[] out) {
        int best = 0;
        double bv = out[0];
        for (int o = 1; o < N_OUT; o++) if (out[o] > bv) { best = o; bv = out[o]; }
        return best;
    }

    static List<Sample> loadDataset(String path) {
        List<Sample> samples = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new FileReader(path))) {
            String line;
            while ((line = br.readLine()) != null) {
                String[] fields = line.trim().split("\\s+");
                int[] ints = new int[fields.length];
                int n = 0;
                for (String tok : fields) {
                    try { ints[n] = Integer.parseInt(tok); n++; } catch (NumberFormatException ignored) {}
                }
                if (n < N_IN + 1) continue;
                double[] px = new double[N_IN];
                for (int i = 0; i < N_IN; i++) px[i] = ints[i] / 16.0;
                samples.add(new Sample(px, ints[N_IN]));
            }
        } catch (IOException ignored) {}
        return samples;
    }

    double accuracy(List<Sample> samples, double[] hidden, double[] out) {
        int correct = 0;
        for (Sample s : samples) {
            forward(s.pixels(), hidden, out);
            if (argmax(out) == s.label()) correct++;
        }
        return correct * 100.0 / samples.size();
    }

    public static void main(String[] args) {
        List<Sample> train = loadDataset("examples/digit_recognizer/optdigits_train.txt");
        List<Sample> test = loadDataset("examples/digit_recognizer/optdigits_test.txt");
        System.out.printf("loaded %d train / %d test samples%n", train.size(), test.size());

        Recognizer net = new Recognizer();
        double[] hidden = new double[N_HID], out = new double[N_OUT], dhidden = new double[N_HID];
        double lr = 0.05;
        for (int epoch = 0; epoch <= 25; epoch++) {
            if (epoch % 5 == 0)
                System.out.printf("epoch %d  test accuracy %s%%%n", epoch, net.accuracy(test, hidden, out));
            for (Sample s : train) {
                net.forward(s.pixels(), hidden, out);
                net.backprop(s.pixels(), s.label(), hidden, out, dhidden, lr);
            }
        }
        System.out.printf("final test accuracy: %s%%%n", net.accuracy(test, hidden, out));
    }
}
