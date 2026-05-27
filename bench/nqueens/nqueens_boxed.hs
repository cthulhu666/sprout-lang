-- N-Queens: boxed Array Int Bool — 1:1 structural match with the Sprout version.
-- Same algorithm, same O(n)-copy-on-update, but elements are heap-boxed pointers.
--
-- Compile: ghc -O2 -o nqueens_hs_boxed nqueens_boxed.hs
-- Run:     ./nqueens_hs_boxed
import Data.Array
import System.CPUTime
import Control.Exception (evaluate)
import Text.Printf

type Board = Array Int Bool

queens :: Int -> Int -> Int -> Board -> Board -> Board -> Int
queens n row col cols pd nd
  | row == n  = 1
  | col >= n  = 0
  | otherwise =
      queens n row (col+1) cols pd nd +
      (if not (cols ! col) && not (pd ! p) && not (nd ! q)
         then queens n (row+1) 0
                (cols // [(col, True)])
                (pd   // [(p,   True)])
                (nd   // [(q,   True)])
         else 0)
  where
    p = row + col
    q = row - col + n - 1

countSolutions :: Int -> Int
countSolutions n =
  queens n 0 0
    (listArray (0, n-1)   (replicate n        False))
    (listArray (0, 2*n-2) (replicate (2*n-1)  False))
    (listArray (0, 2*n-2) (replicate (2*n-1)  False))

benchmark :: Int -> IO ()
benchmark n = do
  t0 <- getCPUTime
  c  <- evaluate (countSolutions n)
  t1 <- getCPUTime
  let ms = fromIntegral (t1 - t0) / 1e9 :: Double
  printf "N=%d: %d  (%.1f ms)\n" n c ms

main :: IO ()
main = mapM_ benchmark [1, 4, 8, 10, 12, 13]
