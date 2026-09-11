"""Unit tests for validator module."""

import tempfile
import unittest
from pathlib import Path
from env_manager.validator import compile_python_file, scan_file_for_emojis


class TestValidator(unittest.TestCase):
    """Test suite for code validation and zero-emoji compliance."""

    def test_compile_python_file_valid(self):
        """Verify valid python file compiles with no error."""
        content = "def test_func():\n    return 42\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            err = compile_python_file(tmp_path)
            self.assertIsNone(err)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_compile_python_file_invalid(self):
        """Verify invalid python file fails compilation."""
        content = "def broken(\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            err = compile_python_file(tmp_path)
            self.assertIsNotNone(err)
            assert err is not None
            self.assertEqual(err.violation_type, "syntax_error")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_emoji_scan_clean(self):
        """Verify clean file produces no violations."""
        content = "Clean source code without any emojis.\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            errs = scan_file_for_emojis(tmp_path)
            self.assertEqual(len(errs), 0)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
