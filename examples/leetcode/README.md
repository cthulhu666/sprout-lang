# LeetCode in Sprout

Solutions to 31 common LeetCode problems, written as idiomatic Sprout. Each file is
self-contained and its `main` prints the result for the problem's statement examples next
to the expected value. Run one with `mise exec -- just run examples/leetcode/p0001_two_sum.sprout`.

## Strings, hash, counting

| # | Title | Tags | File |
|---|---|---|---|
| 3110 | Score of a String | string, pairwise | [p3110_score_of_a_string.sprout](p3110_score_of_a_string.sprout) |
| 1 | Two Sum | hash table | [p0001_two_sum.sprout](p0001_two_sum.sprout) |
| 49 | Group Anagrams | hash table, sorting | [p0049_group_anagrams.sprout](p0049_group_anagrams.sprout) |
| 3 | Longest Substring Without Repeating Characters | sliding window, hash table | [p0003_longest_substring_without_repeating_characters.sprout](p0003_longest_substring_without_repeating_characters.sprout) |
| 20 | Valid Parentheses | stack | [p0020_valid_parentheses.sprout](p0020_valid_parentheses.sprout) |
| 14 | Longest Common Prefix | string, fold | [p0014_longest_common_prefix.sprout](p0014_longest_common_prefix.sprout) |
| 13 | Roman to Integer | string, lookahead | [p0013_roman_to_integer.sprout](p0013_roman_to_integer.sprout) |
| 347 | Top K Frequent Elements | counting, bucket sort | [p0347_top_k_frequent_elements.sprout](p0347_top_k_frequent_elements.sprout) |

## DP, prefix sums, greedy

| # | Title | Tags | File |
|---|---|---|---|
| 53 | Maximum Subarray | DP, Kadane | [p0053_maximum_subarray.sprout](p0053_maximum_subarray.sprout) |
| 121 | Best Time to Buy and Sell Stock | DP, one pass | [p0121_best_time_to_buy_and_sell_stock.sprout](p0121_best_time_to_buy_and_sell_stock.sprout) |
| 70 | Climbing Stairs | DP | [p0070_climbing_stairs.sprout](p0070_climbing_stairs.sprout) |
| 198 | House Robber | DP | [p0198_house_robber.sprout](p0198_house_robber.sprout) |
| 322 | Coin Change | DP, LinVec | [p0322_coin_change.sprout](p0322_coin_change.sprout) |
| 238 | Product of Array Except Self | prefix products, scan | [p0238_product_of_array_except_self.sprout](p0238_product_of_array_except_self.sprout) |
| 560 | Subarray Sum Equals K | prefix sums, hash table | [p0560_subarray_sum_equals_k.sprout](p0560_subarray_sum_equals_k.sprout) |
| 55 | Jump Game | greedy | [p0055_jump_game.sprout](p0055_jump_game.sprout) |

## Sorting, search, bits

| # | Title | Tags | File |
|---|---|---|---|
| 56 | Merge Intervals | sorting, intervals | [p0056_merge_intervals.sprout](p0056_merge_intervals.sprout) |
| 15 | 3Sum | sorting, two pointers | [p0015_3sum.sprout](p0015_3sum.sprout) |
| 42 | Trapping Rain Water | two pointers | [p0042_trapping_rain_water.sprout](p0042_trapping_rain_water.sprout) |
| 300 | Longest Increasing Subsequence | binary search, LinVec | [p0300_longest_increasing_subsequence.sprout](p0300_longest_increasing_subsequence.sprout) |
| 136 | Single Number | bits, XOR | [p0136_single_number.sprout](p0136_single_number.sprout) |
| 338 | Counting Bits | bits, DP | [p0338_counting_bits.sprout](p0338_counting_bits.sprout) |
| 215 | Kth Largest Element in an Array | heap, sorting | [p0215_kth_largest_element_in_an_array.sprout](p0215_kth_largest_element_in_an_array.sprout) |
| 67 | Add Binary | bits, string math | [p0067_add_binary.sprout](p0067_add_binary.sprout) |

## Grid, graph, tree, backtracking

| # | Title | Tags | File |
|---|---|---|---|
| 200 | Number of Islands | grid, DFS | [p0200_number_of_islands.sprout](p0200_number_of_islands.sprout) |
| 54 | Spiral Matrix | grid, simulation | [p0054_spiral_matrix.sprout](p0054_spiral_matrix.sprout) |
| 207 | Course Schedule | graph, topological sort | [p0207_course_schedule.sprout](p0207_course_schedule.sprout) |
| 739 | Daily Temperatures | monotonic stack | [p0739_daily_temperatures.sprout](p0739_daily_temperatures.sprout) |
| 102 | Binary Tree Level Order Traversal | tree, BFS | [p0102_binary_tree_level_order_traversal.sprout](p0102_binary_tree_level_order_traversal.sprout) |
| 78 | Subsets | backtracking | [p0078_subsets.sprout](p0078_subsets.sprout) |
| 46 | Permutations | backtracking | [p0046_permutations.sprout](p0046_permutations.sprout) |
