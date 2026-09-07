import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from . import __version__
from .catalog import read_catalog, export_catalog
from .core import Scanner, Cancelled
from .proposals import build_proposals


def emit(value):
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True)


def write_result(value, output):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if not output:
        print(text)
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(text + "\n")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    actual_args = sys.argv[1:] if argv is None else argv
    if not actual_args:
        from .automatic import run_automatic
        return run_automatic()
    parser = argparse.ArgumentParser(description="GodjiOS discovery bridge (Windows 10/11)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Read installed content; never launch it")
    scan.add_argument("--catalog", help="Optional GodjiOS ZIP or JSON template")
    scan.add_argument("--root", action="append", default=[], help="Additional folder to recursively search (repeatable)")
    scan.add_argument("--all-drives", action="store_true", help="Deep search all local fixed disks; may take a long time")
    scan.add_argument("--steam-root", action="append", help="Steam client directory (repeatable)")
    scan.add_argument("--epic-manifests", action="append", help="Epic manifest directory (repeatable)")
    scan.add_argument("--no-system", action="store_true", help="Disable registry and shortcut discovery, use explicit sources only")
    scan.add_argument("--output", help="Write JSON atomically to this file instead of stdout")
    scan.add_argument("--icons-dir", help="Save local PNG icons in this folder (use the scan output parent for export)")
    scan.add_argument("--cancel-file", help="Create this file to cancel a running scan")
    scan.add_argument("--max-files", type=int, default=200000)
    export = commands.add_parser("export", help="Create a GodjiOS v3 import ZIP")
    export.add_argument("--scan", required=True, help="JSON result from scan")
    export.add_argument("--catalog", help="Same original ZIP used by scan, including covers")
    export.add_argument("--output", required=True, help="New ZIP filename")
    export.add_argument("--approve", action="append", default=[], help="Explicitly approve one reviewed detected item ID (repeatable)")
    proposals = commands.add_parser("proposals", help="Create safe launch-update proposals for a future GodjiOS API")
    proposals.add_argument("--scan", required=True, help="JSON result from scan")
    proposals.add_argument("--catalog", required=True, help="The exact GodjiOS catalog used for scan")
    proposals.add_argument("--machine-id", required=True, help="Stable identifier of the scanned gaming PC")
    proposals.add_argument("--output", help="Write proposal JSON atomically to this file instead of stdout")
    args = parser.parse_args(actual_args)
    try:
        if args.command == "scan":
            if args.max_files < 1:
                raise ValueError("max-files must be positive")
            if args.output and args.catalog and Path(args.output).resolve() == Path(args.catalog).resolve():
                raise ValueError("Output must not overwrite the catalog")
            catalog = read_catalog(args.catalog) if args.catalog else None
            if args.all_drives:
                from .windows import fixed_drives
                args.root.extend(str(p) for p in fixed_drives())
            scanner = Scanner(emit, args.cancel_file, args.max_files)
            result = scanner.run(args.root, catalog, args.steam_root, args.epic_manifests, not args.no_system)
            if args.icons_dir:
                from .icons import extract_icons
                extract_icons(result, args.icons_dir)
            if args.catalog:
                import hashlib
                result["catalogSha256"] = hashlib.sha256(Path(args.catalog).read_bytes()).hexdigest()
            write_result(result, args.output)
        elif args.command == "export":
            result = json.loads(Path(args.scan).read_text(encoding="utf-8-sig"))
            if result.get("schemaVersion") != 1 or not isinstance(result.get("items"), list):
                raise ValueError("Unsupported scan schema")
            if Path(args.output).resolve() == Path(args.scan).resolve():
                raise ValueError("Output must not overwrite the scan")
            if result.get("catalogSha256") and not args.catalog:
                raise ValueError("This scan requires its original catalog for export")
            if args.catalog:
                import hashlib
                if hashlib.sha256(Path(args.catalog).read_bytes()).hexdigest() != result.get("catalogSha256"):
                    raise ValueError("Template differs from the catalog used during scanning")
            known = {i["id"] for i in result["items"]}
            approved = set(args.approve)
            if not approved <= known:
                raise ValueError("Unknown approved item ID")
            for item in result["items"]:
                if item["id"] in approved:
                    item["status"] = "found"
            for match in result.get("matches", []):
                if match.get("itemId") in approved:
                    match["status"] = "found"
            if any(i.get("launch", {}).get("workingDirectory") for i in result["items"]):
                emit({"event": "warning", "source": "export", "message":
                      "workingDirectory is retained in scan JSON but omitted from ZIP: the supplied GodjiOS format has no demonstrated field for it"})
            write_result(export_catalog(result, args.output, args.catalog, assets_root=Path(args.scan).parent), None)
        else:
            result = json.loads(Path(args.scan).read_text(encoding="utf-8-sig"))
            if result.get("schemaVersion") != 1 or not isinstance(result.get("items"), list):
                raise ValueError("Unsupported scan schema")
            catalog = read_catalog(args.catalog)
            write_result(build_proposals(result, catalog, args.machine_id), args.output)
        return 0
    except (Cancelled, KeyboardInterrupt):
        emit({"event": "cancelled"})
        return 130
    except Exception as error:
        emit({"event": "error", "message": str(error), "type": type(error).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
