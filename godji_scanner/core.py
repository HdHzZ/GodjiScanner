import hashlib
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone


def normalize(title):
    return "".join(c for c in title.casefold() if c.isalnum())


def steam_id(path, args):
    if Path(path).name.lower() != "steam.exe":
        return None
    try:
        value = args[args.index("-applaunch") + 1]
        return value if value.isdigit() else None
    except (ValueError, IndexError):
        return None


def parse_vdf(text):
    # Steam KeyValues: tokenize braces, quoted strings and // comments without
    # treating Windows path backslashes as Python unicode escapes.
    tokens = re.finditer(r'//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])', text)
    stream = [m.group(2) if m.group(2) else re.sub(r'\\([\\"])', r'\1', m.group(1))
              for m in tokens if m.group(1) is not None or m.group(2)]
    pos = 0

    def object_value(nested=False):
        nonlocal pos
        out = {}
        while pos < len(stream):
            key = stream[pos]
            pos += 1
            if key == "}":
                if not nested:
                    raise ValueError("Unexpected closing brace")
                return out
            if pos >= len(stream):
                raise ValueError("Missing VDF value")
            value = stream[pos]
            pos += 1
            out[key] = object_value(True) if value == "{" else value
        if nested:
            raise ValueError("Unclosed VDF object")
        return out
    return object_value()


class Cancelled(Exception):
    pass


class Scanner:
    def __init__(self, emit=None, cancel_file=None, max_files=200000):
        self.items = {}
        self.warnings = []
        self.emit = emit or (lambda event: None)
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.max_files = max_files
        self.visited = 0

    def check(self):
        if self.cancel_file and self.cancel_file.exists():
            raise Cancelled()

    def warning(self, source, error):
        event = {"event": "warning", "source": source, "message": str(error)}
        self.warnings.append(event)
        self.emit(event)

    def add(self, title, path, args=None, folder=None, source="filesystem", kind="program",
            confirmed=False, identity=None, working_directory=None, reason=None):
        args = args or []
        key = json.dumps([os.path.normcase(str(path)), args,
                          os.path.normcase(str(working_directory)) if working_directory else None], ensure_ascii=False)
        ident = "detected-" + hashlib.sha256(key.encode()).hexdigest()[:16]
        if ident in self.items:
            item = self.items[ident]
            if source not in item["sources"]:
                item["sources"].append(source)
            if confirmed and item["status"] != "found":
                item.update(title=title, status="found", confidence="high", reason=reason)
            if identity:
                item["identity"] = identity
            return
        self.items[ident] = {
            "id": ident, "title": title, "type": kind,
            "installPath": str(folder) if folder else None,
            "launch": {"path": str(path), "args": args,
                       "workingDirectory": str(working_directory) if working_directory else None},
            "status": "found" if confirmed else "needs_review",
            "confidence": "high" if confirmed else "low", "sources": [source],
            "identity": identity, "reason": reason,
        }

    def steam(self, roots):
        seen = set()
        for root in roots:
            self.check()
            root = Path(root)
            launcher = root / "steam.exe"
            libraries = {root}
            config = root / "steamapps" / "libraryfolders.vdf"
            if config.is_file():
                try:
                    for v in parse_vdf(config.read_text(encoding="utf-8-sig"))["libraryfolders"].values():
                        path = v.get("path") if isinstance(v, dict) else v
                        if isinstance(path, str) and (":" in path or path.startswith("/")):
                            libraries.add(Path(path))
                except (OSError, ValueError, KeyError, TypeError) as e:
                    self.warning("steam", e)
            for library in libraries:
                for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
                    self.check()
                    if manifest in seen:
                        continue
                    seen.add(manifest)
                    try:
                        app = parse_vdf(manifest.read_text(encoding="utf-8-sig"))["AppState"]
                        appid = str(app["appid"])
                        relative = app["installdir"]
                        folder = library / "steamapps" / "common" / relative
                        if not folder.resolve().is_relative_to((library / "steamapps" / "common").resolve()):
                            raise ValueError("Invalid Steam install directory")
                        if not appid.isdigit() or not folder.is_dir() or not launcher.is_file():
                            continue
                        complete = bool(int(app.get("StateFlags", "0")) & 4)
                        self.add(app["name"], launcher, ["-applaunch", appid], folder,
                                 "steam", "game", complete, {"provider": "steam", "appId": appid},
                                 reason="Installed Steam manifest and folder" if complete else "Steam installation may be incomplete")
                    except (OSError, ValueError, KeyError, TypeError) as e:
                        self.warning("steam", f"{manifest}: {e}")

    def epic(self, manifest_dirs):
        for directory in manifest_dirs:
            for manifest in Path(directory).glob("*.item"):
                self.check()
                try:
                    app = json.loads(manifest.read_text(encoding="utf-8-sig"))
                    folder = Path(app["InstallLocation"])
                    exe = folder / app["LaunchExecutable"]
                    if not exe.resolve().is_relative_to(folder.resolve()) or not exe.is_file():
                        continue
                    # Preserve the installed executable as a candidate; authentication
                    # and Epic URI requirements need human confirmation.
                    from .windows import split_args
                    self.add(app["DisplayName"], exe, split_args(app.get("LaunchCommand", "")),
                             folder, "epic", "game", identity={"provider": "epic", "appId": app.get("AppName")},
                             reason="Epic manifest found; launcher/authentication requirements need review")
                except (OSError, ValueError, KeyError, TypeError) as e:
                    self.warning("epic", f"{manifest}: {e}")

    def walk(self, roots):
        skip_dirs = {"windows", "$recycle.bin", "system volume information", "node_modules",
                     "__pycache__", ".git", "winsxs", "installer", "redist", "_commonredist"}
        seen = set()
        visited_dirs = set()
        for root in roots:
            self.check()
            root = Path(root)
            if not root.is_dir():
                self.warning("filesystem", f"Folder is unavailable: {root}")
                continue
            for directory, dirs, files in os.walk(root, followlinks=True,
                                                   onerror=lambda e: self.warning("filesystem", e)):
                self.check()
                canonical = os.path.normcase(str(Path(directory).resolve()))
                if canonical in visited_dirs or canonical.startswith("\\\\"):
                    dirs[:] = []
                    continue
                visited_dirs.add(canonical)
                dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
                for name in files:
                    self.check()
                    self.visited += 1
                    if self.visited > self.max_files:
                        self.warning("filesystem", f"File limit reached ({self.max_files}); results are partial")
                        return
                    if self.visited % 2000 == 0:
                        self.emit({"event": "progress", "source": "filesystem", "filesVisited": self.visited})
                    if Path(name).suffix.lower() not in {".exe", ".bat", ".cmd", ".url"}:
                        continue
                    path = Path(directory) / name
                    if path.suffix.lower() == ".url":
                        from .windows import url_shortcut
                        url_shortcut(self, path)
                        continue
                    key = os.path.normcase(str(path.resolve()))
                    if key in seen:
                        continue
                    seen.add(key)
                    # A binary's name alone cannot establish its user-facing identity.
                    self.add(path.stem, path, folder=path.parent, reason="File candidate; verify title and launch")

    def match(self, catalog):
        results = []
        for entry in catalog["items"]:
            self.check()
            path, args = entry.get("path", ""), entry.get("args", [])
            appid = steam_id(path, args)
            candidates = []
            preserve = False
            if appid:
                candidates = [i for i in self.items.values() if i.get("identity") == {"provider": "steam", "appId": appid}]
                preserve = True
            else:
                # Exact launch path AND arguments avoid confusing games sharing a launcher.
                candidates = [i for i in self.items.values() if path and
                              os.path.normcase(i["launch"]["path"]) == os.path.normcase(path) and
                              i["launch"]["args"] == args and normalize(i["title"]) == normalize(entry["title"])]
                preserve = bool(candidates)
                if not candidates:
                    candidates = [i for i in self.items.values() if normalize(i["title"]) == normalize(entry["title"])]
                if not candidates and path and Path(path).suffix.lower() == ".exe":
                    candidates = [i for i in self.items.values() if
                                  Path(i["launch"]["path"]).name.lower() == Path(path).name.lower() and
                                  i["sources"] == ["filesystem"]]
                    # Filename matches may be generic launchers: never auto-approve.
                    preserve = True
                    results.append({"catalogId": entry["id"], "title": entry["title"],
                                    "status": "needs_review" if candidates else "not_found",
                                    "itemId": candidates[0]["id"] if len(candidates) == 1 else None,
                                    "candidateIds": [i["id"] for i in candidates], "preserveArgs": preserve})
                    continue
            results.append({"catalogId": entry["id"], "title": entry["title"],
                            "status": candidates[0]["status"] if len(candidates) == 1 else
                                      ("needs_review" if candidates else "not_found"),
                            "itemId": candidates[0]["id"] if len(candidates) == 1 else None,
                            "candidateIds": [i["id"] for i in candidates], "preserveArgs": preserve})
        return results

    def template_paths(self, catalog):
        for entry in catalog["items"]:
            self.check()
            path, args = entry.get("path", ""), entry.get("args", [])
            # For Steam games only an installed manifest can establish identity.
            if steam_id(path, args):
                continue
            if path and Path(path).suffix.lower() == ".exe" and Path(path).is_file():
                self.add(entry["title"], path, args, str(Path(path).parent),
                         source="catalog-path", kind=entry.get("type", "program"),
                         reason="Template executable exists; application installation and launch need review")

    def run(self, roots=(), catalog=None, steam_roots=None, epic_dirs=None, system=True):
        started = time.monotonic()
        self.check()
        from . import windows
        if system and os.name != "nt":
            raise ValueError("System discovery requires Windows 10/11")
        if steam_roots is None:
            steam_roots = windows.steam_roots() if system else []
        if epic_dirs is None:
            epic_dirs = [Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"] if system else []
        for name, action in [("steam", lambda: self.steam(steam_roots)),
                             ("epic", lambda: self.epic(epic_dirs)),
                             ("windows", lambda: windows.discover(self) if system else None),
                             ("filesystem", lambda: self.walk(roots))]:
            self.check()
            self.emit({"event": "progress", "source": name, "state": "started"})
            try:
                action()
            except (OSError, ValueError) as e:
                self.warning(name, e)
        if catalog:
            self.template_paths(catalog)
        matches = self.match(catalog) if catalog else []
        from . import __version__
        result = {"schemaVersion": 1, "scannerVersion": __version__,
                  "scannedAt": datetime.now(timezone.utc).isoformat(),
                  "clubId": catalog.get("clubId", "") if catalog else "",
                  "items": sorted(self.items.values(), key=lambda i: i["title"].casefold()),
                  "matches": matches, "warnings": self.warnings,
                  "partial": bool(self.warnings), "durationSeconds": round(time.monotonic() - started, 3)}
        from .presentation import classify
        classify(result)
        self.emit({"event": "completed", "items": len(result["items"]), "partial": result["partial"]})
        return result
