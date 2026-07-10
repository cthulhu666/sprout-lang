{-# LANGUAGE BangPatterns #-}

-- 64 -> 24 -> 10 softsign MLP, MSE, SGD — same algorithm/seed as every other port.
--
-- The "unsafe" Haskell: flat unboxed IOUArray Double weights mutated in place via
-- Data.Array.Base.unsafeRead / unsafeWrite (no bounds checks), manual index arithmetic,
-- strict recursive inner loops. This is Haskell written like C — it discards the immutability
-- and purity the language is built on to buy speed. Contrast Recognizer.hs, the pure port.
-- Identical numerics -> identical 92.67%.

module Main where

import Control.Monad (forM_, when)
import Data.Array.Base (unsafeRead, unsafeWrite)
import Data.Array.IO (IOUArray)
import Data.Array.MArray (newArray)
import Data.IORef (modifyIORef', newIORef, readIORef)
import Text.Printf (printf)

nIn, nHid, nOut :: Int
nIn = 64
nHid = 256
nOut = 10

type Vec = IOUArray Int Double

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
target o l = if o == l then 1.0 else 0.0

-- Flat weight arrays: W1 (h*nIn+i), B1 (h), W2 (o*nHid+h), B2 (o).
data Net = Net { w1 :: !Vec, b1 :: !Vec, w2 :: !Vec, b2 :: !Vec }

data Sample = Sample { pixels :: !Vec, sLabel :: !Int }

-- One LCG sequence threaded through W1 (row-major), B1, W2 (row-major), B2. weight k consumes
-- state s_k: read the current state, emit its weight, then advance.
initNet :: IO Net
initNet = do
  w1' <- newArray (0, nHid * nIn - 1) 0.0
  b1' <- newArray (0, nHid - 1) 0.0
  w2' <- newArray (0, nOut * nHid - 1) 0.0
  b2' <- newArray (0, nOut - 1) 0.0
  sRef <- newIORef (12345 :: Int)
  let nextW = do
        s <- readIORef sRef
        modifyIORef' sRef lcgNext
        pure (lcgWeight s)
  forM_ [0 .. nHid - 1] $ \h -> forM_ [0 .. nIn - 1] $ \i -> nextW >>= unsafeWrite w1' (h * nIn + i)
  forM_ [0 .. nHid - 1] $ \h -> nextW >>= unsafeWrite b1' h
  forM_ [0 .. nOut - 1] $ \o -> forM_ [0 .. nHid - 1] $ \h -> nextW >>= unsafeWrite w2' (o * nHid + h)
  forM_ [0 .. nOut - 1] $ \o -> nextW >>= unsafeWrite b2' o
  pure (Net w1' b1' w2' b2')

forward :: Net -> Vec -> Vec -> Vec -> IO ()
forward net x hidden out = do
  forM_ [0 .. nHid - 1] $ \h -> do
    bh <- unsafeRead (b1 net) h
    let goI !i !z
          | i >= nIn = pure z
          | otherwise = do
              w <- unsafeRead (w1 net) (h * nIn + i)
              xi <- unsafeRead x i
              goI (i + 1) (z + w * xi)
    z <- goI 0 bh
    unsafeWrite hidden h (softsign z)
  forM_ [0 .. nOut - 1] $ \o -> do
    bo <- unsafeRead (b2 net) o
    let goH !h !z
          | h >= nHid = pure z
          | otherwise = do
              w <- unsafeRead (w2 net) (o * nHid + h)
              hh <- unsafeRead hidden h
              goH (h + 1) (z + w * hh)
    z <- goH 0 bo
    unsafeWrite out o z

backprop :: Net -> Vec -> Int -> Vec -> Vec -> Vec -> Double -> IO ()
backprop net x l hidden out dhidden lr = do
  forM_ [0 .. nHid - 1] $ \h -> unsafeWrite dhidden h 0.0
  forM_ [0 .. nOut - 1] $ \o -> do
    oo <- unsafeRead out o
    let dout = 2.0 * (oo - target o l)
    forM_ [0 .. nHid - 1] $ \h -> do
      w <- unsafeRead (w2 net) (o * nHid + h)
      dh <- unsafeRead dhidden h
      unsafeWrite dhidden h (dh + dout * w)
  forM_ [0 .. nOut - 1] $ \o -> do
    oo <- unsafeRead out o
    let dout = 2.0 * (oo - target o l)
    forM_ [0 .. nHid - 1] $ \h -> do
      w <- unsafeRead (w2 net) (o * nHid + h)
      hh <- unsafeRead hidden h
      unsafeWrite (w2 net) (o * nHid + h) (w - lr * dout * hh)
    bo <- unsafeRead (b2 net) o
    unsafeWrite (b2 net) o (bo - lr * dout)
  forM_ [0 .. nHid - 1] $ \h -> do
    dh <- unsafeRead dhidden h
    hh <- unsafeRead hidden h
    let dz = dh * softsignDeriv hh
    forM_ [0 .. nIn - 1] $ \i -> do
      w <- unsafeRead (w1 net) (h * nIn + i)
      xi <- unsafeRead x i
      unsafeWrite (w1 net) (h * nIn + i) (w - lr * dz * xi)
    bh <- unsafeRead (b1 net) h
    unsafeWrite (b1 net) h (bh - lr * dz)

argmax :: Vec -> IO Int
argmax out = do
  v0 <- unsafeRead out 0
  let go !o !bv !bi
        | o >= nOut = pure bi
        | otherwise = do
            v <- unsafeRead out o
            if v > bv then go (o + 1) v o else go (o + 1) bv bi
  go 1 v0 0

accuracy :: Net -> [Sample] -> Vec -> Vec -> IO Double
accuracy net samples hidden out = do
  let count !acc [] = pure acc
      count !acc (s : rest) = do
        forward net (pixels s) hidden out
        best <- argmax out
        count (if best == sLabel s then acc + 1 else acc) rest
  correct <- count (0 :: Int) samples
  pure (fromIntegral correct * 100.0 / fromIntegral (length samples))

loadDataset :: String -> IO [Sample]
loadDataset path = do
  contents <- readFile path
  let rows = [ ints | line <- lines contents
                    , let ints = map read (words line) :: [Int]
                    , length ints >= nIn + 1 ]
  mapM
    (\ints -> do
        px <- newArray (0, nIn - 1) 0.0 :: IO Vec
        forM_ [0 .. nIn - 1] $ \i -> unsafeWrite px i (fromIntegral (ints !! i) / 16.0)
        pure (Sample px (ints !! nIn)))
    rows

main :: IO ()
main = do
  train <- loadDataset "examples/digit_recognizer/optdigits_train.txt"
  test  <- loadDataset "examples/digit_recognizer/optdigits_test.txt"
  printf "loaded %d train / %d test samples\n" (length train) (length test)
  net <- initNet
  hidden  <- newArray (0, nHid - 1) 0.0 :: IO Vec
  out     <- newArray (0, nOut - 1) 0.0 :: IO Vec
  dhidden <- newArray (0, nHid - 1) 0.0 :: IO Vec
  let lr = 0.05
  forM_ [0 .. 25 :: Int] $ \epoch -> do
    when (epoch `mod` 5 == 0) $ do
      acc <- accuracy net test hidden out
      printf "epoch %d  test accuracy %s%%\n" epoch (show acc)
    forM_ train $ \s -> do
      forward net (pixels s) hidden out
      backprop net (pixels s) (sLabel s) hidden out dhidden lr
  acc <- accuracy net test hidden out
  printf "final test accuracy: %s%%\n" (show acc)
