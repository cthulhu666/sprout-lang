from __future__ import annotations

import unittest

# Python interpreter is being removed; these tests are skipped pending full removal.


@unittest.skip("Python interpreter being removed")
class CliReplTestsProxy(unittest.TestCase):
    def test_placeholder(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
