import io
import os
import tempfile
import unittest
from unittest.mock import patch

from app import app
from decode import decode
from encode import encode


class ExpiryIntegrationTests(unittest.TestCase):
    def test_decode_rejects_expired_file(self):
        raw = b"expiry-check" * 500

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=300)

            with patch("decode.time", return_value=9_999_999_999):
                with self.assertRaisesRegex(ValueError, "This file has expired"):
                    decode(pgn_path, output_path)

    def test_decode_allows_unexpired_file(self):
        raw = b"expiry-check" * 500

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=3600)
            decode(pgn_path, output_path)

            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)


class ExpiryRouteTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_decode_route_rejects_expired_file(self):
        raw = b"route-expiry-check" * 400

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=300)

            with open(pgn_path, "rb") as f:
                with patch("decode.time", return_value=9_999_999_999):
                    response = self.client.post(
                        "/decode",
                        data={
                            "file_type": "text",
                            "file": (io.BytesIO(f.read()), "expired.pgn"),
                        },
                        content_type="multipart/form-data",
                    )

            self.assertEqual(response.status_code, 400)
            payload = response.get_json() or {}
            self.assertIn("This file has expired", payload.get("error", ""))

    def test_expiry_time_hidden_from_visible_header(self):
        raw = b"hidden-expiry-check" * 200

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=300)

            with open(pgn_path, "r", encoding="utf-8") as f:
                pgn_text = f.read()

            header_block = pgn_text.split("\n\n", 1)[0]
            self.assertNotIn('[ExpiryTime "', header_block)


if __name__ == "__main__":
    unittest.main()