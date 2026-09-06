"""Create a handoff bundle without local scans or the club's private catalog."""
from pathlib import Path
import hashlib
import zipfile
from godji_scanner import __version__

root = Path(__file__).resolve().parent
exe = root / "dist/GodjiScanner.exe"
if not exe.is_file():
    raise SystemExit("Run build.ps1 first")
bundle = root / f"dist/GodjiScanner-{__version__}-win-x64.zip"
files = [root / name for name in ["README.md", "scanner.py", "build.ps1", "requirements-build.txt", "package_release.py"]]
for folder in ["godji_scanner", "docs", "examples", "tests"]:
    files.extend(p for p in (root / folder).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts)
with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.write(exe, "GodjiScanner.exe")
    archive.writestr("EXE-SHA256.txt", hashlib.sha256(exe.read_bytes()).hexdigest() + "  GodjiScanner.exe\n")
    for file in files:
        archive.write(file, file.relative_to(root).as_posix())
print(bundle)
