import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from godji_scanner.catalog import export_catalog, read_catalog
from godji_scanner.core import Scanner, Cancelled, parse_vdf
from godji_scanner.cli import main
from godji_scanner.windows import split_args


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def steam(self, installed=True, state=4):
        client = self.root / "Steam"
        client.mkdir(exist_ok=True)
        (client / "steam.exe").touch()
        library = self.root / "Games with spaces"
        apps = library / "steamapps"
        apps.mkdir(parents=True, exist_ok=True)
        if installed:
            (apps / "common" / "Counter Strike").mkdir(parents=True, exist_ok=True)
        (client / "steamapps").mkdir(exist_ok=True)
        quoted = str(library).replace("\\", "\\\\")
        (client / "steamapps/libraryfolders.vdf").write_text(
            '"libraryfolders" { "0" { "path" "' + quoted + '" "label" "" } }', encoding="utf-8")
        (apps / "appmanifest_730.acf").write_text(
            f'"AppState" {{ "appid" "730" "name" "Counter-Strike 2" "installdir" "Counter Strike" "StateFlags" "{state}" }}', encoding="utf-8")
        return client

    def template(self, cover="covers/cs.png"):
        path = self.root / "template.zip"
        catalog = {"version": 3, "clubId": "test", "categories": [{"id": "shooter", "label": "Шутеры"}],
                   "items": [{"id": "cs", "title": "Counter-Strike 2", "type": "game", "visible": True,
                              "path": str(self.root / "Old/steam.exe"), "args": ["-applaunch", "730", "-cafeapplaunch"],
                              "coverFile": cover, "categories": ["shooter"]},
                             {"id": "missing", "title": "Missing game", "type": "game", "visible": True}]}
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("catalog.json", json.dumps(catalog))
            z.writestr(cover, b"test image bytes")
        return path

    def test_vdf_empty_strings_comments_and_backslashes(self):
        parsed = parse_vdf('// comment\n"root" { "empty" "" "path" "D:\\\\Games" }')
        self.assertEqual(parsed["root"], {"empty": "", "path": "D:\\Games"})

    def test_steam_secondary_library_and_template_roundtrip(self):
        client = self.steam()
        template = self.template()
        result = Scanner().run(catalog=read_catalog(template), steam_roots=[client], system=False)
        self.assertEqual(result["matches"][0]["status"], "found")
        output = self.root / "out.zip"
        export_catalog(result, output, template)
        catalog = read_catalog(output)
        self.assertEqual(catalog["items"][0]["path"], str(client / "steam.exe"))
        self.assertEqual(catalog["items"][0]["args"], ["-applaunch", "730", "-cafeapplaunch"])
        self.assertFalse(catalog["items"][1]["visible"])
        with zipfile.ZipFile(output) as z:
            self.assertEqual(z.read("covers/cs.png"), b"test image bytes")

    def test_launcher_without_game_not_found(self):
        client = self.steam(installed=False)
        result = Scanner().run(catalog=read_catalog(self.template()), steam_roots=[client], system=False)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["matches"][0]["status"], "not_found")

    def test_incomplete_steam_requires_review(self):
        client = self.steam(state=2)
        result = Scanner().run(steam_roots=[client], system=False)
        self.assertEqual(result["items"][0]["status"], "needs_review")

    def test_ambiguous_executables_not_auto_exported(self):
        for folder in ["a", "b"]:
            (self.root / folder).mkdir()
            (self.root / folder / "game.exe").touch()
        catalog = {"items": [{"id": "game", "title": "Game", "path": "X:/game.exe"}]}
        result = Scanner().run(roots=[self.root], catalog=catalog, system=False)
        self.assertEqual(result["matches"][0]["status"], "needs_review")
        self.assertIsNone(result["matches"][0]["itemId"])
        output = self.root / "empty.zip"
        export_catalog(result, output)
        self.assertEqual(read_catalog(output)["items"], [])

    def test_same_launcher_different_games_do_not_match(self):
        scan = Scanner()
        scan.add("Riot Client", "C:/Riot/RiotClientServices.exe", source="shortcut")
        matches = scan.match({"items": [{"id": "valorant", "title": "VALORANT", "path": "C:/Riot/RiotClientServices.exe",
                                         "args": ["--launch-product=valorant"]}]})
        self.assertEqual(matches[0]["status"], "not_found")

    def test_cancellation_and_file_limit(self):
        cancel = self.root / "cancel"
        cancel.touch()
        with self.assertRaises(Cancelled):
            Scanner(cancel_file=cancel).run(system=False)
        for name in ["a.exe", "b.exe"]:
            (self.root / name).touch()
        result = Scanner(max_files=1).run(roots=[self.root], system=False)
        self.assertTrue(result["partial"])

    def test_unsafe_cover_rejected_without_partial_zip(self):
        template = self.template("covers/../../escape.png")
        result = Scanner().run(catalog=read_catalog(template), system=False)
        target = self.root / "out.zip"
        with self.assertRaises(ValueError):
            export_catalog(result, target, template)
        self.assertFalse(target.exists())

    def test_existing_output_never_overwritten(self):
        target = self.root / "out.zip"
        target.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            export_catalog({"items": [], "scannedAt": "test"}, target)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_windows_quoted_arguments(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows command line parser")
        self.assertEqual(split_args('--profile "Клуб тест" --flag ""'), ["--profile", "Клуб тест", "--flag", ""])

    def test_catalog_path_is_only_a_candidate(self):
        exe = self.root / "portable.exe"
        exe.touch()
        catalog = {"items": [{"id": "portable", "title": "Portable app", "path": str(exe)}]}
        result = Scanner().run(catalog=catalog, system=False)
        self.assertEqual(result["matches"][0]["status"], "needs_review")
        self.assertEqual(result["items"][0]["sources"], ["catalog-path"])

    def test_cli_scan_and_approved_export(self):
        (self.root / "Game.exe").touch()
        output = self.root / "scan.json"
        self.assertEqual(main(["scan", "--no-system", "--root", str(self.root), "--output", str(output)]), 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        dest = self.root / "new.zip"
        self.assertEqual(main(["export", "--scan", str(output), "--output", str(dest),
                               "--approve", result["items"][0]["id"]]), 0)
        self.assertEqual(len(read_catalog(dest)["items"]), 1)


if __name__ == "__main__":
    unittest.main()
