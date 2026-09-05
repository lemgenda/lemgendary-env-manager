"""Unit tests for requirements_manager module."""

import tempfile
import unittest
from pathlib import Path
from env_manager.requirements_manager import parse_requirements_file


class TestRequirementsManager(unittest.TestCase):
    """Test suite for requirements parser and manager."""

    def test_parse_requirements_file(self):
        """Verify parsing of requirements lines including comments and markers."""
        content = """
        # Sample requirements
        --extra-index-url https://download.pytorch.org/whl/cu121
        torch>=2.4.0
        MetaTrader5>=5.0.45; sys_platform == 'win32'
        transformers>=4.44.0
        """
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            parsed = parse_requirements_file(tmp_path)
            names = [p.name for p in parsed]
            self.assertIn("__index_url__", names)
            self.assertIn("torch", names)
            self.assertIn("metatrader5", names)
            self.assertIn("transformers", names)

            mt5_entry = next(p for p in parsed if p.name == "metatrader5")
            self.assertIsNotNone(mt5_entry.marker)
            self.assertIn("win32", mt5_entry.marker)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
