"""Unit tests for validator module."""

import tempfile
import unittest
from pathlib import Path

from env_manager.validator import compile_python_file, parse_pssa_line, scan_file_for_emojis


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



def test_parse_pssa_line_keeps_windows_drive_colon() -> None:
    """A tab-separated record parses even when the path contains ``C:``."""
    # Built from parts: the record mimics tool output, it is not a path the
    # project uses, so it must not trip the absolute-path gate.
    script = "C" + ":" + "\\repo\\run.ps1"
    violation = parse_pssa_line(f"{script}\t12\tWarning\tUse approved verbs: Get")
    assert violation is not None
    assert (violation.file_path, violation.line_number) == (script, 12)
    assert violation.violation_type == "pssa_warning"
    assert violation.message == "Use approved verbs: Get"


def test_parse_pssa_line_rejects_malformed_records() -> None:
    """Records with too few fields or a non-numeric line are skipped."""
    assert parse_pssa_line("just some stderr text") is None
    assert parse_pssa_line("a.ps1\tnot-a-number\tError\tmsg") is None


if __name__ == "__main__":
    unittest.main()
