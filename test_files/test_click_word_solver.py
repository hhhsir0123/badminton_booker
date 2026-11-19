"""点选验证码匹配逻辑测试（不加载模型）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.click_word_solver import ClickWordSolver


class MatchTest(unittest.TestCase):
    def test_orders_by_words(self):
        chars = ["屋", "成", "站", "三"]
        centers = [(10, 10), (20, 20), (30, 30), (40, 40)]
        self.assertEqual(
            ClickWordSolver.match(chars, centers, ["成", "三", "屋"]),
            [(20, 20), (40, 40), (10, 10)],
        )

    def test_missing_char_returns_none(self):
        chars = ["成", "站", "三"]
        centers = [(1, 1), (2, 2), (3, 3)]
        self.assertIsNone(ClickWordSolver.match(chars, centers, ["成", "三", "屋"]))

    def test_duplicate_target_uses_distinct_boxes(self):
        chars = ["成", "成", "三"]
        centers = [(1, 1), (9, 9), (3, 3)]
        self.assertEqual(ClickWordSolver.match(chars, centers, ["成", "三"]), [(1, 1), (3, 3)])

    def test_single_target(self):
        chars = ["成", "站"]
        centers = [(5, 5), (6, 6)]
        self.assertEqual(ClickWordSolver.match(chars, centers, ["站"]), [(6, 6)])


if __name__ == "__main__":
    unittest.main()
