import io
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from app import app
from decode import decode
from encode import encode
from decode import restore_technical_headers_from_comment
from encode import hide_technical_headers_in_comment


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

    def test_decode_rejects_tampered_expiry_when_server_signing_enabled(self):
        raw = b"tamper-expiry-check" * 300
        signing_secret = "server-side-signing-secret"

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            tampered_path = os.path.join(tmp, "tampered.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=3600, expiry_signing_secret=signing_secret)

            with open(pgn_path, "r", encoding="utf-8") as f:
                original_pgn = f.read()

            restored_headers = restore_technical_headers_from_comment(original_pgn)
            tampered_headers = re.sub(
                r'(\[ExpiryTime\s+")(\d+)("\])',
                lambda m: f'{m.group(1)}{int(m.group(2)) + 7200}{m.group(3)}',
                restored_headers,
                count=1,
            )
            tampered_pgn = hide_technical_headers_in_comment(
                tampered_headers,
                carrier_style="whitespace",
            )

            with open(tampered_path, "w", encoding="utf-8") as f:
                f.write(tampered_pgn)

            with self.assertRaisesRegex(ValueError, "signature is invalid"):
                decode(tampered_path, output_path, expiry_signing_secret=signing_secret)

    def test_decode_rejects_unsigned_expiry_when_signing_enabled(self):
        raw = b"unsigned-expiry-check" * 200
        signing_secret = "server-side-signing-secret"

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=300)

            with self.assertRaisesRegex(ValueError, "signature is missing"):
                decode(pgn_path, output_path, expiry_signing_secret=signing_secret)

    def test_decode_allows_unsigned_expiry_when_legacy_flag_enabled(self):
        raw = b"unsigned-expiry-legacy-check" * 200
        signing_secret = "server-side-signing-secret"

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, self_destruct_timer=3600)

            decode(
                pgn_path,
                output_path,
                expiry_signing_secret=signing_secret,
                allow_unsigned_expiry=True,
            )

            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)


class ExpiryRouteTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_decode_route_rejects_expired_file(self):
        raw = b"route-expiry-check" * 400
        signing_secret = "server-side-signing-secret"

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(
                input_path,
                pgn_path,
                self_destruct_timer=300,
                expiry_signing_secret=signing_secret,
            )

            previous_signing_key = os.environ.get("ROOKHIDE_EXPIRY_SIGNING_KEY")
            with open(pgn_path, "rb") as f:
                try:
                    with patch("decode.time", return_value=9_999_999_999):
                        os.environ["ROOKHIDE_EXPIRY_SIGNING_KEY"] = signing_secret
                        response = self.client.post(
                            "/decode",
                            data={
                                "file_type": "text",
                                "file": (io.BytesIO(f.read()), "expired.pgn"),
                            },
                            content_type="multipart/form-data",
                        )
                finally:
                    if previous_signing_key is None:
                        os.environ.pop("ROOKHIDE_EXPIRY_SIGNING_KEY", None)
                    else:
                        os.environ["ROOKHIDE_EXPIRY_SIGNING_KEY"] = previous_signing_key

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