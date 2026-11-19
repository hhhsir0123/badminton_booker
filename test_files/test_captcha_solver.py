"""验证码求解的纯逻辑测试：不开浏览器、不联网。"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.captcha_solver import CaptchaSolver


class FakeOcr:
    def __init__(self, mapping):
        self.mapping = mapping

    def classification(self, payload):
        return self.mapping.get(payload, "")


class FakeRecognizer:
    def __init__(self, mapping):
        self._ocr = FakeOcr(mapping)

    @property
    def ocr(self):
        return self._ocr


def make_solver(mapping, **kwargs):
    solver = CaptchaSolver(None, FakeRecognizer(mapping), **kwargs)
    return solver


def stub_variants(solver, items):
    solver.variants = lambda payload: items


class NormalizeTest(unittest.TestCase):
    def test_strips_noise(self):
        solver = make_solver({})
        self.assertEqual(solver._normalize(" a b 1 2\n"), "ab12")
        self.assertEqual(solver._normalize("3+8=?"), "38")
        self.assertEqual(solver._normalize(None), "")


class VoteTest(unittest.TestCase):
    def test_majority_wins(self):
        solver = make_solver({b"a": "ab12", b"b": "AB12", b"c": "zzzz"})
        stub_variants(solver, [("a", b"a"), ("b", b"b"), ("c", b"c")])
        self.assertEqual(solver.recognize(b"x"), "ab12")

    def test_case_insensitive_vote_keeps_first_form(self):
        solver = make_solver({b"a": "AbCd", b"b": "ABCD", b"c": "abce"})
        stub_variants(solver, [("a", b"a"), ("b", b"b"), ("c", b"c")])
        self.assertEqual(solver.recognize(b"x"), "AbCd")

    def test_length_filter(self):
        solver = make_solver({b"a": "12", b"b": "abcd"})
        stub_variants(solver, [("a", b"a"), ("b", b"b")])
        self.assertEqual(solver.recognize(b"x"), "abcd")

    def test_all_invalid_returns_none(self):
        solver = make_solver({b"a": "1", b"b": "abcdefgh"})
        stub_variants(solver, [("a", b"a"), ("b", b"b")])
        self.assertIsNone(solver.recognize(b"x"))

    def test_single_vote_still_used(self):
        solver = make_solver({b"a": "ab12", b"b": "zzzz", b"c": "yyyy"})
        stub_variants(solver, [("a", b"a"), ("b", b"b"), ("c", b"c")])
        self.assertEqual(solver.recognize(b"x"), "ab12")


try:
    from PIL import Image  # noqa: F401

    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "需要 Pillow 才能测试预处理")
class VariantsTest(unittest.TestCase):
    def test_variants_are_png_and_distinct(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (80, 30), "white")
        draw = ImageDraw.Draw(image)
        draw.text((8, 8), "A1B2", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        variants = CaptchaSolver.variants(buffer.getvalue())
        self.assertGreaterEqual(len(variants), 3)
        names = [name for name, _ in variants]
        self.assertEqual(names[0], "raw")
        self.assertIn("otsu2x", names)
        for _, payload in variants:
            self.assertTrue(payload.startswith(b"\x89PNG"))

    def test_otsu_threshold_in_range(self):
        from PIL import Image

        gray = Image.new("L", (10, 10), 0)
        threshold = CaptchaSolver._otsu(gray)
        self.assertTrue(0 <= threshold <= 255)


if __name__ == "__main__":
    unittest.main()


class _FakeImage:
    def __init__(self, width, height, visible=True):
        self.size = {"width": width, "height": height}
        self._visible = visible

    def is_displayed(self):
        return self._visible


class ImageSizeFilterTest(unittest.TestCase):
    """验证码图片要按尺寸过滤：页面上的 16x16 图标不能被当成验证码。"""

    def _solver(self):
        from src.utils.captcha_solver import CaptchaSolver

        return CaptchaSolver.__new__(CaptchaSolver)

    def test_rejects_tiny_icon(self):
        self.assertFalse(self._solver()._looks_like_captcha(_FakeImage(16, 16)))

    def test_accepts_captcha(self):
        self.assertTrue(self._solver()._looks_like_captcha(_FakeImage(80, 26)))

    def test_rejects_hidden(self):
        self.assertFalse(self._solver()._looks_like_captcha(_FakeImage(80, 26, visible=False)))
