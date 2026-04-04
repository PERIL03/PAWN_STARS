import unittest
import os
import bz2
import gzip
import lzma
import zlib
import tempfile

from encode import prepare_payload_for_encoding, encode
from decode import decode
from decode import maybe_decompress_payload


class CompressionBehaviorTests(unittest.TestCase):
    def test_prepare_payload_uses_compression_when_helpful(self):
        data = (b"A" * 4096) + (b"B" * 4096)
        payload, meta = prepare_payload_for_encoding(data)
        self.assertLess(len(payload), len(data))
        self.assertTrue(meta["compression_used"])
        self.assertIn(meta["compression_algorithm"], {"zlib", "bz2", "lzma"})

    def test_prepare_payload_skips_compression_for_random_like_data(self):
        data = zlib.compress(os.urandom(4096), 9)
        payload, meta = prepare_payload_for_encoding(data)
        self.assertEqual(payload, data)
        self.assertFalse(meta["compression_used"])
        self.assertEqual(meta["compression_algorithm"], "none")

    def test_prepare_payload_disables_compression_when_level_zero(self):
        data = (b"A" * 4096) + (b"B" * 4096)
        payload, meta = prepare_payload_for_encoding(data, compression_level=0)
        self.assertEqual(payload, data)
        self.assertFalse(meta["compression_requested"])

    def test_maybe_decompress_payload_round_trip(self):
        raw = b"rookhide" * 300
        compressed = zlib.compress(raw, 9)
        restored = maybe_decompress_payload(
            compressed,
            {"Compression": "zlib", "OriginalSize": str(len(raw))},
        )
        self.assertEqual(restored, raw)

    def test_maybe_decompress_payload_supports_bz2(self):
        raw = b"rookhide-bz2" * 300
        compressed = bz2.compress(raw, compresslevel=9)
        restored = maybe_decompress_payload(
            compressed,
            {"Compression": "bz2", "OriginalSize": str(len(raw))},
        )
        self.assertEqual(restored, raw)

    def test_maybe_decompress_payload_supports_lzma(self):
        raw = b"rookhide-lzma" * 300
        compressed = lzma.compress(raw, preset=9)
        restored = maybe_decompress_payload(
            compressed,
            {"Compression": "lzma", "OriginalSize": str(len(raw))},
        )
        self.assertEqual(restored, raw)

    def test_maybe_decompress_payload_rejects_invalid_size_header(self):
        raw = b"rookhide" * 200
        compressed = zlib.compress(raw, 9)
        with self.assertRaises(ValueError):
            maybe_decompress_payload(
                compressed,
                {"Compression": "zlib", "OriginalSize": str(len(raw) + 1)},
            )

    def test_encode_decode_round_trip_preserves_original_bytes(self):
        raw = (b"compress-me-please\n" * 2000)

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)

            with open(pgn_path, "r", encoding="utf-8") as f:
                pgn_text = f.read()

            self.assertNotIn('[Compression "zlib"]', pgn_text)
            self.assertNotIn('[OriginalSize "', pgn_text)

            decode(pgn_path, output_path)
            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)

    def test_technical_headers_hidden_from_pgn_headers(self):
        raw = (b"hidden-headers-check\n" * 600)

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(
                input_path,
                pgn_path,
                engine_guided=True,
                opening_camouflage=True,
            )

            with open(pgn_path, "r", encoding="utf-8") as f:
                pgn_text = f.read()

            header_block = pgn_text.split("\n\n", 1)[0]
            self.assertNotIn('[Seed "', header_block)
            self.assertNotIn('[DataBits "', header_block)
            self.assertNotIn('[Engine "', header_block)
            self.assertNotIn('[OpeningBookUCI "', header_block)

            decode(pgn_path, output_path)
            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)

    def test_encode_decode_round_trip_with_password_and_stealth_modes(self):
        raw = (b"secret-payload\n" * 1200)

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(
                input_path,
                pgn_path,
                password="strongpassword123",
                compression_level=6,
                engine_guided=True,
                opening_camouflage=True,
                metadata_payload="team-internal-note",
            )

            with open(pgn_path, "r", encoding="utf-8") as f:
                pgn_text = f.read()

            self.assertNotIn('[Encryption "', pgn_text)
            self.assertNotIn('[KDF "', pgn_text)
            self.assertNotIn('[KDFSalt "', pgn_text)
            self.assertNotIn('[EngineGuided "heuristic-v1"]', pgn_text)
            self.assertNotIn('[OpeningBookUCI "', pgn_text)
            self.assertNotIn('RH:', pgn_text)

            decode(pgn_path, output_path, password="strongpassword123")
            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)

    def test_decode_fails_when_password_missing_for_encrypted_file(self):
        raw = b"top-secret-data" * 200

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, password="strongpassword123")

            with self.assertRaises(ValueError):
                decode(pgn_path, output_path)

    def test_decode_supports_gzip_wrapped_pgn(self):
        raw = (b"gzip-transfer-check\n" * 1400)

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            pgn_gz_path = os.path.join(tmp, "encoded.pgn.gz")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)

            with open(pgn_path, "rb") as src:
                wrapped = gzip.compress(src.read(), compresslevel=9, mtime=0)
            with open(pgn_gz_path, "wb") as dst:
                dst.write(wrapped)

            decode(pgn_gz_path, output_path)
            with open(output_path, "rb") as f:
                restored = f.read()

            self.assertEqual(restored, raw)


if __name__ == "__main__":
    unittest.main()
