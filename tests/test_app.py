import io
import os
import unittest

from app import (
    app,
    validate_compression_level,
    validate_custom_headers,
    validate_password,
    validate_self_destruct_timer,
)


class ValidationHelpersTests(unittest.TestCase):
    def test_validate_self_destruct_timer_accepts_none(self):
        ok, timer, err = validate_self_destruct_timer(None)
        self.assertTrue(ok)
        self.assertIsNone(timer)
        self.assertIsNone(err)

    def test_validate_self_destruct_timer_rejects_non_integer(self):
        ok, timer, err = validate_self_destruct_timer("abc")
        self.assertFalse(ok)
        self.assertIsNone(timer)
        self.assertIn("Invalid self-destruct timer", err)

    def test_validate_self_destruct_timer_rejects_out_of_range(self):
        ok, timer, err = validate_self_destruct_timer("0")
        self.assertFalse(ok)
        self.assertIsNone(timer)
        self.assertIn("between 1 and 31536000", err)

    def test_validate_custom_headers_date_format(self):
        ok, err = validate_custom_headers({"Date": "2026-03-25"})
        self.assertFalse(ok)
        self.assertIn("YYYY.MM.DD", err)

    def test_validate_custom_headers_elo_range(self):
        ok, err = validate_custom_headers({"WhiteElo": "500"})
        self.assertFalse(ok)
        self.assertIn("between 600 and 3500", err)

    def test_validate_custom_headers_result_values(self):
        ok, err = validate_custom_headers({"Result": "2-0"})
        self.assertFalse(ok)
        self.assertIn("must be one of", err)

    def test_validate_compression_level_rejects_out_of_range(self):
        ok, level, err = validate_compression_level("11")
        self.assertFalse(ok)
        self.assertIsNone(level)
        self.assertIn("between 0 and 9", err)

    def test_validate_password_rejects_short_password(self):
        ok, pwd, err = validate_password("short")
        self.assertFalse(ok)
        self.assertIsNone(pwd)
        self.assertIn("at least 8", err)


class AppRoutesTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("status", payload)
        self.assertIn("rust_engine_available", payload)
        self.assertIn("disk_free_mb", payload)

    def test_contact_without_access_key(self):
        prev = os.environ.get("WEB3FORMS_ACCESS_KEY")
        try:
            if "WEB3FORMS_ACCESS_KEY" in os.environ:
                del os.environ["WEB3FORMS_ACCESS_KEY"]
            response = self.client.post(
                "/contact",
                data={"name": "A", "email": "a@example.com", "message": "Hello"},
            )
            self.assertEqual(response.status_code, 503)
        finally:
            if prev is not None:
                os.environ["WEB3FORMS_ACCESS_KEY"] = prev

    def test_encode_rejects_invalid_date_header(self):
        response = self.client.post(
            "/encode",
            data={
                "file_type": "text",
                "self_destruct_timer": "60",
                "pgn_date": "2026-03-25",
                "file": (io.BytesIO(b"abc"), "sample.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("YYYY.MM.DD", payload.get("error", ""))

    def test_encode_rejects_invalid_timer(self):
        response = self.client.post(
            "/encode",
            data={
                "file_type": "text",
                "self_destruct_timer": "0",
                "file": (io.BytesIO(b"abc"), "sample.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("between 1 and 31536000", payload.get("error", ""))

    def test_decode_rejects_non_pgn_file(self):
        response = self.client.post(
            "/decode",
            data={
                "file_type": "text",
                "file": (io.BytesIO(b"abc"), "sample.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("Decode only accepts .pgn or .pgn.gz", payload.get("error", ""))

    def test_encode_rejects_short_encryption_password(self):
        response = self.client.post(
            "/encode",
            data={
                "file_type": "text",
                "encryption_password": "short",
                "file": (io.BytesIO(b"abc"), "sample.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("at least 8", payload.get("error", ""))


if __name__ == "__main__":
    unittest.main()
