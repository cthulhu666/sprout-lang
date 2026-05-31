{-# LANGUAGE BangPatterns #-}
-- A* on a 100×100 grid — mutable IOUArray implementation.
-- Uses the same wall rule and sorted-list open set as Sprout.
-- g_score and closed use IOUArray Int for O(1) indexed updates.
-- Timing: wall-clock via Data.Time.Clock.System (monotonic, nanosecond precision).
--
-- Compile: ghc -O2 -o astar_hs astar.hs
-- Run:     ./astar_hs
import Data.Array.IO
import Data.Time.Clock.System (getSystemTime, SystemTime(..))
import Text.Printf

w, goalX, goalY, inf, iters :: Int
w = 100; goalX = 99; goalY = 99; inf = 9999999; iters = 60000

isWall :: Int -> Int -> Bool
isWall x y
  | x <= 0 || y <= 0 || x >= goalX || y >= goalY = False
  | otherwise = (x * 5 + y * 3) `mod` 13 < 4

heur :: Int -> Int -> Int
heur x y = abs (goalX - x) + abs (goalY - y)

-- Sorted insertion ascending by f.
openInsert :: (Int,Int,Int,Int) -> [(Int,Int,Int,Int)] -> [(Int,Int,Int,Int)]
openInsert e@(!f,_,_,_) [] = [e]
openInsert e@(!f,_,_,_) all@(h@(!hf,_,_,_):rest)
  | f <= hf   = e : all
  | otherwise = h : openInsert e rest

astar :: IO Int
astar = do
    gScore <- newArray (0, w*w-1) inf :: IO (IOUArray Int Int)
    closed <- newArray (0, w*w-1) False :: IO (IOUArray Int Bool)
    writeArray gScore 0 0
    go [(heur 0 0, 0, 0, 0)] gScore closed
  where
    go [] _ _ = return (-1)
    go ((_,!g,!x,!y):rest) gs cl = do
      if x == goalX && y == goalY
        then return g
        else do
          let idx = y*w+x
          isCl <- readArray cl idx
          if isCl
            then go rest gs cl
            else do
              writeArray cl idx True
              open' <- foldl (stepNeighbor g gs cl) (return rest)
                         [(x,y-1),(x,y+1),(x-1,y),(x+1,y)]
              go open' gs cl

    stepNeighbor !g gs cl macc (!nx,!ny)
      | nx < 0 || nx >= w || ny < 0 || ny >= w = macc
      | isWall nx ny = macc
      | otherwise = do
          acc <- macc
          let nidx = ny*w+nx
          isCl <- readArray cl nidx
          if isCl then return acc
          else do
            eg <- readArray gs nidx
            let !ng = g + 1
            if ng < eg
              then do
                writeArray gs nidx ng
                return $! openInsert (ng + heur nx ny, ng, nx, ny) acc
              else return acc

toNs :: SystemTime -> Integer
toNs (MkSystemTime s ns) = fromIntegral s * 1_000_000_000 + fromIntegral ns

benchmark :: IO ()
benchmark = do
    -- warmup
    mapM_ (const astar) [1..5 :: Int]
    t0 <- getSystemTime
    total <- sum <$> mapM (const astar) [1..iters :: Int]
    t1 <- getSystemTime
    let ms = fromIntegral (toNs t1 - toNs t0) / 1_000_000 :: Double
        usPerRun = round (ms * 1000 / fromIntegral iters) :: Int
    printf "A* 100x100, %d runs: %.1f ms  (%d us/run, path=%d steps)\n"
           iters ms usPerRun (total `div` iters)

main :: IO ()
main = benchmark
