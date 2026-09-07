import unittest

from godji_scanner.review import create_review, default_target


class ReviewTests(unittest.TestCase):
    def test_recommended_items_are_selected_and_server_tools_are_separate(self):
        scan = {"scannerVersion": "test", "scannedAt": "2026-09-07T00:00:00Z", "clubId": "club-1", "items": [
            {"id": "game", "title": "Game", "displayGroup": "applications", "launch": {"path": "D:/Games/game.exe"}},
            {"id": "word", "title": "Word", "displayGroup": "candidates", "launch": {"path": "C:/Office/word.exe"}},
            {"id": "ccboot", "title": "CCBoot", "displayGroup": "applications", "launch": {"path": "C:/CCBoot/CCBoot.exe"}},
        ]}
        review = create_review(scan)
        items = {item["itemId"]: item for item in review["items"]}
        self.assertTrue(items["game"]["selected"])
        self.assertFalse(items["word"]["selected"])
        self.assertEqual(items["game"]["target"], "client")
        self.assertEqual(items["ccboot"]["target"], "server")

    def test_existing_choices_are_preserved(self):
        scan = {"items": [{"id": "app", "title": "App", "displayGroup": "applications", "launch": {"path": "C:/app.exe"}}]}
        existing = {"items": [{"itemId": "app", "selected": False, "target": "both"}]}
        item = create_review(scan, existing)["items"][0]
        self.assertFalse(item["selected"])
        self.assertEqual(item["target"], "both")

    def test_server_detection_uses_title_or_path(self):
        self.assertEqual(default_target({"title": "ClubMonitor", "launch": {"path": "C:/apps/monitor.exe"}}), "server")
        self.assertEqual(default_target({"title": "Launcher", "launch": {"path": "C:/server/launcher.exe"}}), "server")


if __name__ == "__main__":
    unittest.main()
