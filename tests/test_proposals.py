import unittest

from godji_scanner.proposals import build_proposals


class ProposalTests(unittest.TestCase):
    def test_only_changed_confirmed_match_becomes_proposal(self):
        catalog = {"clubId": "club-1", "items": [
            {"id": "nvidia", "title": "NVIDIA Control Panel", "path": "B:/old.exe", "args": []},
            {"id": "unknown", "title": "Unknown", "path": "C:/old.exe", "args": []},
        ]}
        scan = {"schemaVersion": 1, "scannerVersion": "test", "scannedAt": "2026-09-07T00:00:00Z",
                "items": [
                    {"id": "found", "confidence": "high", "launch": {"path": "C:/NVIDIA/nvcplui.exe", "args": [], "workingDirectory": "C:/NVIDIA", "kind": "executable"}},
                    {"id": "review", "confidence": "low", "launch": {"path": "C:/other.exe", "args": []}},
                ],
                "matches": [
                    {"catalogId": "nvidia", "status": "found", "itemId": "found", "preserveArgs": False},
                    {"catalogId": "unknown", "status": "needs_review", "itemId": "review", "preserveArgs": False},
                ]}
        plan = build_proposals(scan, catalog, "pc-46")
        self.assertEqual(plan["clubId"], "club-1")
        self.assertEqual(len(plan["proposals"]), 1)
        item = plan["proposals"][0]
        self.assertEqual(item["applicationId"], "nvidia")
        self.assertEqual(item["previousPath"], "B:/old.exe")
        self.assertEqual(item["launch"]["path"], "C:/NVIDIA/nvcplui.exe")
        self.assertEqual(item["machineId"], "pc-46")

    def test_unchanged_launch_is_not_proposed_and_steam_args_are_preserved(self):
        catalog = {"items": [{"id": "game", "title": "Game", "path": "C:/Steam/steam.exe", "args": ["-applaunch", "10", "-cafe"]}]}
        scan = {"items": [{"id": "found", "confidence": "high", "launch": {"path": "C:/Steam/steam.exe", "args": ["-applaunch", "10"]}}],
                "matches": [{"catalogId": "game", "status": "found", "itemId": "found", "preserveArgs": True}]}
        self.assertEqual(build_proposals(scan, catalog, "pc-1")["proposals"], [])


if __name__ == "__main__":
    unittest.main()
