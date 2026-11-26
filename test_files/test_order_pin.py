"""orderPin 算法与下单 payload 的单元测试（不访问平台）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.order_pin import build_order_pin, build_submit_payload, decrypt_order_pin


class OrderPinTest(unittest.TestCase):
    def test_hex_and_length(self):
        pin = build_order_pin(512, 384)
        self.assertEqual(len(pin), 32)
        int(pin, 16)  # 必须是合法 hex

    def test_roundtrip(self):
        self.assertEqual(decrypt_order_pin(build_order_pin(120, 40)), "120,40")

    def test_different_clicks_differ(self):
        self.assertNotEqual(build_order_pin(10, 20), build_order_pin(20, 10))

    def test_matches_openssl_vector(self):
        # openssl enc -aes-128-cbc -K c1h2i5n6g2o2k4a7 -iv C2H3I4N5G2O3K1E4
        self.assertEqual(build_order_pin(512, 384), "20acc57d323c0fcdfea5ee4c40a2d1e6")


class PayloadTest(unittest.TestCase):
    def test_fields(self):
        payload = build_submit_payload(
            venue_site_id=3, day="2026-11-29", space_id="23", time_id="15994",
            price=60.0, buddy_ids="998138", phone="17396154630",
        )
        self.assertEqual(payload["venueSiteId"], 3)
        self.assertEqual(payload["reservationDate"], "2026-11-29")
        self.assertEqual(payload["reservationOrderJson"], '[{"spaceId":"23","timeId":"15994"}]')
        self.assertEqual(payload["buddyIds"], "998138")
        self.assertEqual(payload["orderPrice"], 60.0)
        self.assertEqual(len(payload["orderPin"]), 32)
        self.assertEqual(payload["reservationType"], -1)
