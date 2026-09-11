import copy
import json
from pathlib import Path, PurePosixPath
import zipfile

MAX_JSON = 16 * 1024 * 1024


def read_catalog(path):
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("catalog.json")
            if entry.file_size > MAX_JSON:
                raise ValueError("catalog.json exceeds 16 MB")
            raw = archive.read(entry)
    else:
        if path.stat().st_size > MAX_JSON:
            raise ValueError("Catalog exceeds 16 MB")
        raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Expected an object with an items array")
    if data.get("version", 3) != 3:
        raise ValueError("Only GodjiOS catalog version 3 is supported")
    ids = set()
    for item in data["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            raise ValueError("Every catalog item must have a string title")
        if not isinstance(item.get("id"), str) or item["id"] in ids:
            raise ValueError("Catalog item IDs must be unique strings")
        ids.add(item["id"])
        if "path" in item and not isinstance(item["path"], str):
            raise ValueError("path must be a string")
        if "args" in item and (not isinstance(item["args"], list) or
                               any(not isinstance(a, str) for a in item["args"])):
            raise ValueError("args must be an array of strings")
    return data


def retain_catalog_matches(result):
    """Keep diagnostics only for entries that belong to the selected club catalog."""
    matched_ids = {match.get("itemId") for match in result.get("matches", []) if match.get("itemId")}
    result["items"] = [item for item in result.get("items", []) if item.get("id") in matched_ids]
    from .presentation import classify
    classify(result)
    return result


def safe_cover(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and len(p.parts) == 2 and p.parts[0] == "covers"
            and ".." not in p.parts and "\\" not in name and ":" not in name
            and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".ico"})


def export_catalog(result, destination, template=None, include_reviewed=False, assets_root=None):
    """Never mutate the input archive; unresolved entries are retained but hidden."""
    dest = Path(destination)
    if template and dest.resolve() == Path(template).resolve():
        raise ValueError("Output must not overwrite the template")
    if dest.exists():
        raise ValueError("Output already exists; choose a new path")
    original = read_catalog(template) if template else None
    if original:
        data = copy.deepcopy(original)
        matches = {m["catalogId"]: m for m in result.get("matches", [])}
        expected = {i["id"] for i in data["items"]}
        if set(matches) != expected:
            raise ValueError("Scan matches do not correspond to this template")
        by_id = {i["id"]: i for i in result["items"]}
        for item in data["items"]:
            match = matches[item["id"]]
            found = by_id.get(match.get("itemId"))
            allowed = found and (match["status"] == "found" or
                                (include_reviewed and match["status"] == "needs_review"))
            if allowed:
                # Arguments from the template may be game-specific: retain them only
                # when the same executable/Steam identity is matched.
                item["path"] = found["launch"]["path"]
                if not match.get("preserveArgs"):
                    item["args"] = found["launch"]["args"]
                item["visible"] = item.get("visible", True)
            # A scan can miss an installed program (for example, because its
            # launcher is per-user or a disk was offline). Keep the club's
            # existing Shell visibility; discovery must never hide content.
    else:
        data = {"version": 3, "clubId": result.get("clubId", ""), "categories": [], "items": []}
        for found in result["items"]:
            if found["status"] != "found" and not include_reviewed:
                continue
            data["items"].append({"id": found["id"], "title": found["title"],
                                  "type": found["type"],
                                  "typeLabel": "Игра" if found["type"] == "game" else "Программа",
                                  "categories": [], "path": found["launch"]["path"],
                                  "args": found["launch"]["args"], "tone": "dark", "visible": True})
    local_covers = {}
    if assets_root:
        import re
        by_id = {i["id"]: i for i in result["items"]}
        matches = {m["catalogId"]: m.get("itemId") for m in result.get("matches", [])}
        for index, item in enumerate(data["items"]):
            source = by_id.get(matches.get(item["id"]) if original else item["id"], {})
            icon = source.get("iconFile", "")
            if item.get("coverFile") or not re.fullmatch(r"icons/icon-\d+\.png", icon):
                continue
            path = (Path(assets_root) / icon).resolve()
            if path.is_relative_to(Path(assets_root).resolve()) and path.is_file() and path.stat().st_size < 1024 * 1024:
                name = f"covers/local-{index}.png"
                item["coverFile"] = name
                local_covers[name] = path
    data["exportedAt"] = result["scannedAt"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as out:
            created = True
            for name, path in local_covers.items():
                out.writestr(name, path.read_bytes())
            names = {i.get("coverFile") for i in data["items"] if i.get("coverFile")} - set(local_covers)
            if names:
                if not template or not zipfile.is_zipfile(template):
                    raise ValueError("To preserve covers, provide the original ZIP template")
                with zipfile.ZipFile(template) as src:
                    total = 0
                    for name in sorted(names):
                        if not safe_cover(name):
                            raise ValueError("Unsafe cover path")
                        info = src.getinfo(name)
                        total += info.file_size
                        if info.file_size > 20 * 1024 * 1024 or total > 512 * 1024 * 1024:
                            raise ValueError("Cover size limit exceeded")
                        out.writestr(name, src.read(info))
            out.writestr("catalog.json", json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        if created:
            dest.unlink(missing_ok=True)
        raise
    return {"output": str(dest.resolve()), "items": len(data["items"]),
            "visible": sum(bool(i.get("visible")) for i in data["items"])}
