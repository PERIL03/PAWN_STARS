import unittest

from encode import verify_pgn_content


class VerifyPgnContentTests(unittest.TestCase):
    def test_verify_pgn_content_passes_when_headers_present(self):
        content = '[Event "World Championship"]\n[ExpiryTime "1700000000"]\n'
        verify_pgn_content(content, 1700000000, {"Event": "World Championship"})

    def test_verify_pgn_content_fails_missing_expiry(self):
        content = '[Event "World Championship"]\n'
        with self.assertRaises(ValueError):
            verify_pgn_content(content, 1700000000, None)

    def test_verify_pgn_content_fails_missing_custom_header(self):
        content = '[Event "Test"]\n'
        with self.assertRaises(ValueError):
            verify_pgn_content(content, None, {"White": "Carlsen"})


if __name__ == "__main__":
    unittest.main()
