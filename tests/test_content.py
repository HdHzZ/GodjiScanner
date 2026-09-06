import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from godji_scanner.core import Scanner
from godji_scanner.presentation import classify
from godji_scanner.windows import settings
from godji_scanner.catalog import export_catalog, read_catalog


class ContentTests(unittest.TestCase):
    def test_launch_arguments_and_working_directories_remain_distinct(self):
        scanner = Scanner()
        scanner.add("Game A", "C:/launcher.exe", ["--game", "a"], source="shortcut")
        scanner.add("Game B", "C:/launcher.exe", ["--game", "b"], source="shortcut")
        scanner.add("Game A profile", "C:/launcher.exe", ["--game", "a"], source="shortcut", working_directory="D:/Profile")
        self.assertEqual(len(scanner.items), 3)

    def test_components_grouped_but_peripheral_apps_preserved(self):
        scanner = Scanner()
        scanner.add("Game", "S:/steam/steam.exe", ["-applaunch", "123"], folder="D:/Games/Game",
                    source="steam", kind="game", identity={"provider": "steam", "appId": "123"})
        scanner.add("game", "D:/Games/Game/Binaries/game.exe")
        scanner.add("QtWebEngineProcess", "D:/Tools/QtWebEngineProcess.exe")
        scanner.add("LAMZU Driver", "D:/LAMZU/Mouse Drive Beta.exe", source="shortcut")
        scanner.add("Unknown mouse tool", "D:/Other/OemDrv.exe")
        result = classify({"items": list(scanner.items.values())})
        by_name = {i["title"]: i for i in result["items"]}
        self.assertEqual(by_name["game"]["displayGroup"], "components")
        self.assertEqual(by_name["QtWebEngineProcess"]["displayGroup"], "components")
        self.assertEqual(by_name["LAMZU Driver"]["displayGroup"], "applications")
        self.assertEqual(by_name["Unknown mouse tool"]["displayGroup"], "candidates")
        self.assertEqual(len(result["items"]), 5)

    def test_scripts_and_url_are_discovered_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mouse.bat").write_text("exit /b 42")
            (root / "launch.cmd").write_text("exit /b 42")
            (root / "Warface.url").write_text("[InternetShortcut]\nURL=mailrugames://play/0.1177", encoding="utf-8")
            result = Scanner().run(roots=[root], system=False)
            self.assertEqual(len(result["items"]), 3)
            self.assertEqual({i["launch"]["kind"] for i in result["items"]}, {"script", "uri"})
            self.assertTrue(all(i["status"] == "needs_review" for i in result["items"]))

    def test_directory_link_cycle_terminates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "game.exe").touch()
            try:
                os.symlink(root, root / "loop", target_is_directory=True)
            except OSError:
                self.skipTest("OS does not grant symbolic link creation")
            try:
                result = Scanner(max_files=100).run(roots=[root], system=False)
                self.assertEqual(len(result["items"]), 1)
                self.assertFalse(result["partial"])
            finally:
                (root / "loop").unlink()

    def test_system_commands_are_separate_launches(self):
        scanner = Scanner()
        settings(scanner)
        self.assertGreaterEqual(len(scanner.items), 5)
        self.assertIn("ms-settings:display-advanced", [i["launch"]["path"] for i in scanner.items.values()])

    def test_web_and_duplicate_steam_urls_are_not_main_cards(self):
        scanner = Scanner()
        scanner.add("Game", "S:/steam/steam.exe", ["-applaunch", "123"], folder="S:/steamapps/common/Game",
                    source="steam", kind="game", confirmed=True, identity={"provider": "steam", "appId": "123"})
        scanner.add("Game shortcut", "steam://rungameid/123", source="url-shortcut")
        scanner.add("EULA", "https://example.invalid/eula", source="url-shortcut")
        result = classify({"items": list(scanner.items.values())})
        by_title = {i["title"]: i for i in result["items"]}
        self.assertEqual(by_title["Game shortcut"]["displayGroup"], "components")
        self.assertEqual(by_title["Game shortcut"]["relatedTo"], by_title["Game"]["id"])
        self.assertEqual(by_title["EULA"]["displayGroup"], "components")

    def test_generated_icon_is_included_in_clean_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "icons").mkdir()
            (root / "icons/icon-0.png").write_bytes(b"png-test")
            scanner = Scanner()
            scanner.add("Game", "C:/Game.exe", confirmed=True)
            item = next(iter(scanner.items.values()))
            item["iconFile"] = "icons/icon-0.png"
            result = {"items": [item], "scannedAt": "test"}
            archive = root / "out.zip"
            export_catalog(result, archive, assets_root=root)
            catalog = read_catalog(archive)
            name = catalog["items"][0]["coverFile"]
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.read(name), b"png-test")


if __name__ == "__main__":
    unittest.main()
