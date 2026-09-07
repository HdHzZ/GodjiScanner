"""Create API-neutral, safe launch update proposals from a scan result."""

from datetime import datetime, timezone
import os


def _different(left, right):
    return os.path.normcase(str(left or "")) != os.path.normcase(str(right or ""))


def build_proposals(scan, catalog, machine_id):
    """Return only unambiguous high-confidence launch-path changes.

    The result deliberately contains no network code. A future GodjiOS agent can
    submit it to the write API, while operators can inspect it first.
    """
    if not machine_id or not machine_id.strip():
        raise ValueError("machine-id must not be empty")
    catalog_items = {item.get("id"): item for item in catalog.get("items", [])}
    detected = {item.get("id"): item for item in scan.get("items", [])}
    proposals = []

    for match in scan.get("matches", []):
        if match.get("status") != "found" or not match.get("itemId"):
            continue
        original = catalog_items.get(match.get("catalogId"))
        found = detected.get(match.get("itemId"))
        if not original or not found or found.get("confidence") != "high":
            continue
        launch = found.get("launch", {})
        path = launch.get("path")
        if not isinstance(path, str) or not path:
            continue
        args = original.get("args", []) if match.get("preserveArgs") else launch.get("args", [])
        old_path, old_args = original.get("path", ""), original.get("args", [])
        if not _different(path, old_path) and args == old_args:
            continue
        proposals.append({
            "applicationId": original["id"],
            "title": original.get("title", ""),
            "previousPath": old_path,
            "previousArgs": old_args,
            "launch": {
                "path": path,
                "args": args,
                "workingDirectory": launch.get("workingDirectory"),
                "launchType": launch.get("kind", "executable"),
            },
            "source": "godji-scanner",
            "confidence": "high",
            "machineId": machine_id.strip(),
            "scanId": scan.get("scanId") or scan.get("scannedAt"),
        })

    return {
        "schemaVersion": 1,
        "kind": "godjios-launch-proposals",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "clubId": catalog.get("clubId", scan.get("clubId", "")),
        "machineId": machine_id.strip(),
        "scannerVersion": scan.get("scannerVersion", ""),
        "scanId": scan.get("scanId") or scan.get("scannedAt"),
        "proposals": proposals,
    }
