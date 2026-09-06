import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from godji_scanner.automatic import run_automatic, save_reports
from godji_scanner.cli import main
from godji_scanner.core import Scanner, Cancelled


class AutomaticTests(unittest.TestCase):
    def test_no_arguments_selects_automatic_mode(self):
        with patch("godji_scanner.automatic.run_automatic", return_value=0) as automatic:
            self.assertEqual(main([]), 0)
            automatic.assert_called_once_with()

    def test_without_catalog_produces_reports_with_real_executable_path(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            games = base / "Игры"
            games.mkdir()
            exe = games / "Game.exe"
            exe.touch()
            with patch("godji_scanner.windows.fixed_drives", return_value=[games]):
                self.assertEqual(run_automatic(base=base, system=False, pause=False), 0)
            folder = next((base / "results").iterdir())
            data = json.loads((folder / "scan.json").read_text(encoding="utf-8"))
            self.assertEqual(data["items"][0]["launch"]["path"], str(exe))
            self.assertEqual(data["matches"], [])
            for name in ["report.html", "applications.csv", "scan.log"]:
                self.assertTrue((folder / name).is_file())

    def test_cancelled_scan_keeps_partial_results(self):
        with tempfile.TemporaryDirectory() as temp:
            def interrupted(scanner, **kwargs):
                scanner.add("Test", "C:/Game.exe")
                raise Cancelled()
            with patch.object(Scanner, "run", interrupted):
                self.assertEqual(run_automatic(base=temp, roots=[temp], system=False, pause=False), 130)
            folder = next((Path(temp) / "results").iterdir())
            data = json.loads((folder / "scan.json").read_text(encoding="utf-8"))
            self.assertTrue(data["partial"])
            self.assertTrue(data["cancelled"])
            self.assertEqual(len(data["items"]), 1)

    def test_reports_escape_discovered_text(self):
        with tempfile.TemporaryDirectory() as temp:
            scanner = Scanner()
            scanner.add('<script>alert(1)</script>', 'C:/test.exe')
            scanner.add('=1+1', 'C:/other.exe')
            result = {"items": list(scanner.items.values()), "partial": False, "warnings": []}
            save_reports(result, Path(temp))
            html = (Path(temp) / "report.html").read_text(encoding="utf-8")
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            csv = (Path(temp) / "applications.csv").read_text(encoding="utf-8-sig")
            self.assertIn("'=1+1", csv)


if __name__ == "__main__":
    unittest.main()
