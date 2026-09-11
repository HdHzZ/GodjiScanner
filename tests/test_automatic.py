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
        with patch("godji_scanner.cli.available_catalogs", return_value=[]):
            with patch("godji_scanner.automatic.run_automatic", return_value=0) as automatic:
                self.assertEqual(main([]), 0)
                automatic.assert_called_once_with(open_review=True)

    def test_no_arguments_selects_club_window_when_catalogs_exist(self):
        with patch("godji_scanner.cli.available_catalogs", return_value=[{"label": "Бор"}]):
            with patch("godji_scanner.cli.run_club_selector", return_value=0) as clubs:
                self.assertEqual(main([]), 0)
                clubs.assert_called_once()

    def test_without_catalog_produces_reports_with_real_executable_path(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            games = base / "Игры"
            games.mkdir()
            exe = games / "Game.exe"
            exe.touch()
            with patch("godji_scanner.windows.fixed_drives", return_value=[games]):
                self.assertEqual(run_automatic(base=base, system=False, pause=False, open_review=False), 0)
            folder = next((base / "results").iterdir())
            data = json.loads((folder / "scan.json").read_text(encoding="utf-8"))
            self.assertEqual(data["items"][0]["launch"]["path"], str(exe))
            self.assertEqual(data["matches"], [])
            for name in ["report.html", "applications.csv", "scan.log"]:
                self.assertTrue((folder / name).is_file())

    def test_automatic_mode_opens_manual_review(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            with patch("godji_scanner.review.run_review") as review:
                with patch("godji_scanner.windows.fixed_drives", return_value=[]):
                    self.assertEqual(run_automatic(base=base, system=False, pause=False, open_review=True), 0)
            result_dir = next((base / "results").iterdir())
            review.assert_called_once()
            self.assertEqual(review.call_args.args[1], result_dir / "review.json")

    def test_scan_is_saved_before_optional_icon_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            def completed_scan(scanner, **_):
                scanner.add("NVIDIA App", "C:/NVIDIA/NVIDIA App.exe", source="shortcut")
                return {"items": list(scanner.items.values()), "partial": False, "warnings": []}
            def icons_after_scan(_, folder):
                self.assertTrue((Path(folder) / "scan.json").is_file())
                raise RuntimeError("icon service unavailable")
            with patch.object(Scanner, "run", completed_scan):
                with patch("godji_scanner.icons.extract_icons", icons_after_scan):
                    self.assertEqual(run_automatic(base=base, roots=[], system=True, pause=False), 0)
            data = json.loads((next((base / "results").iterdir()) / "scan.json").read_text(encoding="utf-8"))
            self.assertEqual(data["items"][0]["title"], "NVIDIA App")
            self.assertTrue(any(w["source"] == "icons" for w in data["warnings"]))

    def test_cancelled_scan_keeps_partial_results(self):
        with tempfile.TemporaryDirectory() as temp:
            def interrupted(scanner, **kwargs):
                scanner.add("Test", "C:/Game.exe")
                raise Cancelled()
            with patch.object(Scanner, "run", interrupted):
                self.assertEqual(run_automatic(base=temp, roots=[temp], system=False, pause=False, open_review=False), 130)
            folder = next((Path(temp) / "results").iterdir())
            data = json.loads((folder / "scan.json").read_text(encoding="utf-8"))
            self.assertTrue(data["partial"])
            self.assertTrue(data["cancelled"])
            self.assertEqual(len(data["items"]), 1)

    def test_reports_escape_discovered_text(self):
        with tempfile.TemporaryDirectory() as temp:
            scanner = Scanner()
            scanner.add('<script>alert(1)</script>', 'C:/test.exe')
            scanner.add('=1+1', 'C:/NVIDIA/NVIDIA App.exe')
            scanner.add('Word', 'C:/Office/WINWORD.EXE', source='shortcut')
            result = {"items": list(scanner.items.values()), "partial": False, "warnings": []}
            save_reports(result, Path(temp))
            html = (Path(temp) / "report.html").read_text(encoding="utf-8")
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            csv = (Path(temp) / "applications.csv").read_text(encoding="utf-8-sig")
            self.assertIn("'=1+1", csv)
            self.assertNotIn("Word", csv)


if __name__ == "__main__":
    unittest.main()
