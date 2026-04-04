import os
import random
import re
import tempfile
import unittest

from decode import decode
from encode import encode


class MetadataCarrierTests(unittest.TestCase):
    def _read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _write_text(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_comment_carrier_is_present_and_hidden_headers_removed(self):
        raw = b"carrier-smoke-check\n" * 200

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)
            pgn_text = self._read_text(pgn_path)

            self.assertIn("{RHMDv3:", pgn_text)
            non_empty = [line for line in pgn_text.splitlines() if line.strip()]
            self.assertTrue(non_empty[-1].strip().startswith("{RHMDv3:"))
            header_block = pgn_text.split("\n\n", 1)[0]
            self.assertNotIn('[Seed "', header_block)
            self.assertNotIn('[DataBits "', header_block)
            self.assertNotIn('[Engine "', header_block)

            decode(pgn_path, output_path)
            with open(output_path, "rb") as f:
                restored = f.read()
            self.assertEqual(restored, raw)

    def test_tampered_carrier_rejected_with_integrity_error(self):
        raw = b"carrier-integrity-check\n" * 200

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)
            text = self._read_text(pgn_path)
            m = re.search(r"\{RHMDv3:([A-Za-z0-9_\-=]+)\}", text)
            self.assertIsNotNone(m)

            payload = m.group(1)
            idx = len(payload) // 2
            new_char = "A" if payload[idx] != "A" else "B"
            tampered_payload = payload[:idx] + new_char + payload[idx + 1:]
            tampered_text = text.replace(payload, tampered_payload, 1)
            self._write_text(pgn_path, tampered_text)

            with self.assertRaisesRegex(ValueError, "Metadata|integrity|authentication"):
                decode(pgn_path, output_path)

    def test_authenticated_carrier_detects_visible_move_tampering(self):
        raw = b"carrier-auth-check\n" * 240

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path, password="strongpassword123")
            text = self._read_text(pgn_path)

            tampered_text = text.replace('[Event "?"]', '[Event "X"]', 1)
            self._write_text(pgn_path, tampered_text)

            with self.assertRaisesRegex(ValueError, "Metadata authentication failed"):
                decode(pgn_path, output_path, password="strongpassword123")

    def test_malformed_carrier_is_explicitly_rejected(self):
        raw = b"carrier-malformed-check\n" * 180

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")
            output_path = os.path.join(tmp, "decoded.bin")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)
            text = self._read_text(pgn_path)
            m = re.search(r"\{RHMDv3:[A-Za-z0-9_\-=]+\}", text)
            self.assertIsNotNone(m)

            malformed = m.group(0)[:-4] + "}"
            self._write_text(pgn_path, text.replace(m.group(0), malformed, 1))

            with self.assertRaisesRegex(ValueError, "Metadata carrier"):
                decode(pgn_path, output_path)

    def test_adversarial_single_char_fuzz_on_carrier(self):
        raw = b"carrier-fuzz-check\n" * 220

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.bin")
            pgn_path = os.path.join(tmp, "encoded.pgn")

            with open(input_path, "wb") as f:
                f.write(raw)

            encode(input_path, pgn_path)
            original = self._read_text(pgn_path)
            m = re.search(r"\{RHMDv3:([A-Za-z0-9_\-=]+)\}", original)
            self.assertIsNotNone(m)

            payload = m.group(1)
            chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-="
            random.seed(12345)

            for _ in range(10):
                i = random.randrange(0, len(payload))
                replacement = random.choice(chars.replace(payload[i], ""))
                mutated = payload[:i] + replacement + payload[i + 1:]
                mutated_text = original.replace(payload, mutated, 1)

                with open(pgn_path, "w", encoding="utf-8") as f:
                    f.write(mutated_text)

                with self.assertRaises(ValueError):
                    decode(pgn_path, os.path.join(tmp, "out.bin"))


if __name__ == "__main__":
    unittest.main()
