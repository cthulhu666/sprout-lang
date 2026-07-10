{-# LANGUAGE BangPatterns #-}

-- 64 -> 24 -> 10 softsign MLP, MSE, SGD — same algorithm/seed as every other port.
--
-- The "pure as fuck" Haskell: immutable [Double] weights, a Net record threaded through
-- foldl', zip/foldl' instead of any indexing or mutation. Every SGD step allocates a whole
-- new Net. deepseq forces each epoch so the lazy-list thunks don't leak across 26 passes —
-- that's strictness, not impurity. Accumulation is seeded with the bias, so the numerics are
-- bit-identical to the mutable ports and it reaches the same 92.67%.
-- See RecognizerUnsafe.hs for the in-place unboxed-array variant.

module Main where

import Control.DeepSeq (NFData (..), deepseq)
import Control.Monad (foldM, when)
import Data.List (foldl', transpose)
import Text.Printf (printf)

nIn, nHid, nOut :: Int
nIn = 64
nHid = 256
nOut = 10

dabs :: Double -> Double
dabs x = if x < 0 then -x else x

softsign :: Double -> Double
softsign x = x / (1.0 + dabs x)

softsignDeriv :: Double -> Double
softsignDeriv s = let d = 1.0 - dabs s in d * d

lcgNext :: Int -> Int
lcgNext s = (1664525 * s + 1013904223) `mod` 2147483648

lcgWeight :: Int -> Double
lcgWeight s = fromIntegral (s `mod` 2000 - 1000) / 10000.0

target :: Int -> Int -> Double
target o label = if o == label then 1.0 else 0.0

data Net = Net
  { w1 :: [[Double]] -- nHid x nIn
  , b1 :: [Double]   -- nHid
  , w2 :: [[Double]] -- nOut x nHid
  , b2 :: [Double]   -- nOut
  }

data Sample = Sample { pixels :: [Double], label :: Int }

chunksOf :: Int -> [a] -> [[a]]
chunksOf _ [] = []
chunksOf n xs = let (a, b) = splitAt n xs in a : chunksOf n b

-- One LCG sequence threaded through W1 (row-major), B1, W2 (row-major), B2 — weight k
-- consumes state s_k, matching the imperative ports.
initNet :: Net
initNet = Net (chunksOf nIn w1flat) b1' (chunksOf nHid w2flat) b2'
  where
    ws = map lcgWeight (iterate lcgNext 12345)
    (w1flat, r1) = splitAt (nHid * nIn) ws
    (b1', r2)    = splitAt nHid r1
    (w2flat, r3) = splitAt (nOut * nHid) r2
    (b2', _)     = splitAt nOut r3

-- Dot product seeded with the bias: reproduces the imperative `z = bias; z += w*x` order.
dotBias :: [Double] -> [Double] -> Double -> Double
dotBias row v bias = foldl' (\ !z (w, x) -> z + w * x) bias (zip row v)

forward :: Net -> [Double] -> ([Double], [Double])
forward net xs = (hidden, out)
  where
    hidden = zipWith (\row bias -> softsign (dotBias row xs bias)) (w1 net) (b1 net)
    out    = zipWith (\row bias -> dotBias row hidden bias) (w2 net) (b2 net)

trainOne :: Double -> Net -> Sample -> Net
trainOne lr net sample = Net newW1 newB1 newW2 newB2
  where
    xs = pixels sample
    l = label sample
    (hidden, out) = forward net xs
    douts = zipWith (\o o' -> 2.0 * (o' - target o l)) [0 ..] out
    -- dhidden h = Σ_o dout_o * w2_o_h, accumulated in o order from 0.0.
    dhidden = map (\col -> foldl' (+) 0.0 (zipWith (*) douts col)) (transpose (w2 net))
    newW2 = zipWith (\row dout -> zipWith (\w hid -> w - lr * dout * hid) row hidden) (w2 net) douts
    newB2 = zipWith (\b dout -> b - lr * dout) (b2 net) douts
    dz = zipWith (\hid dh -> dh * softsignDeriv hid) hidden dhidden
    newW1 = zipWith (\row d -> zipWith (\w xi -> w - lr * d * xi) row xs) (w1 net) dz
    newB1 = zipWith (\b d -> b - lr * d) (b1 net) dz

argmax :: [Double] -> Int
argmax out = snd (foldl' step (head out, 0) (zip out [0 ..]))
  where step (bv, bi) (v, i) = if v > bv then (v, i) else (bv, bi)

accuracy :: Net -> [Sample] -> Double
accuracy net samples =
  fromIntegral correct * 100.0 / fromIntegral (length samples)
  where correct = length (filter (\s -> argmax (snd (forward net (pixels s))) == label s) samples)

loadDataset :: String -> IO [Sample]
loadDataset path = do
  contents <- readFile path
  pure [ Sample (map ((/ 16.0) . fromIntegral) (take nIn ints)) (ints !! nIn)
       | line <- lines contents
       , let ints = map read (words line) :: [Int]
       , length ints >= nIn + 1 ]

main :: IO ()
main = do
  train <- loadDataset "examples/digit_recognizer/optdigits_train.txt"
  test  <- loadDataset "examples/digit_recognizer/optdigits_test.txt"
  printf "loaded %d train / %d test samples\n" (length train) (length test)
  let lr = 0.05
  final <- foldM
    (\net epoch -> do
        when (epoch `mod` 5 == 0) $
          printf "epoch %d  test accuracy %s%%\n" (epoch :: Int) (show (accuracy net test))
        let net' = foldl' (trainOne lr) net train
        net' `deepseq` pure net')
    initNet
    [0 .. 25 :: Int]
  printf "final test accuracy: %s%%\n" (show (accuracy final test))

instance NFData Net where
  rnf (Net a b c d) = a `deepseq` b `deepseq` c `deepseq` d `deepseq` ()
