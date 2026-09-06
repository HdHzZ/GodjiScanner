"""Windows adapters. No discovered executable, shortcut or URI is launched."""
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def split_args(command):
    if not command:
        return []
    if os.name != "nt":
        # Non-Windows use is limited to tests without complex command lines.
        import shlex
        return shlex.split(command, posix=False)
    argc = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    pointer = parse("placeholder.exe " + command, ctypes.byref(argc))
    if not pointer:
        raise ValueError("Invalid Windows command line")
    try:
        return [pointer[i] for i in range(1, argc.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(pointer)


def steam_roots():
    import winreg
    roots = set()
    for hive, key, value in [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]:
        for view in [winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY]:
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    roots.add(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    for env in ["ProgramFiles(x86)", "ProgramFiles"]:
        if os.environ.get(env):
            roots.add(Path(os.environ[env]) / "Steam")
    return sorted(roots)


def fixed_drives():
    """Only local fixed disks; do not automatically traverse network shares."""
    if os.name != "nt":
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    drive_type = ctypes.windll.kernel32.GetDriveTypeW
    drive_type.argtypes = [ctypes.c_wchar_p]
    drive_type.restype = ctypes.c_uint
    return [Path(f"{chr(65 + index)}:/") for index in range(26)
            if mask & (1 << index) and drive_type(f"{chr(65 + index)}:\\") == 3]


def registry_apps(scanner):
    import winreg
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    seen = set()
    for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for view in [winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY]:
            try:
                root = winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    scanner.check()
                    try:
                        key = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, key) as entry:
                            def get(name, default=""):
                                try:
                                    return winreg.QueryValueEx(entry, name)[0]
                                except OSError:
                                    return default
                            title, icon, folder = get("DisplayName"), get("DisplayIcon"), get("InstallLocation")
                            if not title or get("SystemComponent", 0) or get("ParentKeyName"):
                                continue
                            # DisplayIcon is evidence, not a guaranteed launch command.
                            icon = re.sub(r",\s*-?\d+\s*$", "", str(icon)).strip().strip('"')
                            icon = os.path.expandvars(icon)
                            if not icon.lower().endswith(".exe") or not Path(icon).is_file():
                                continue
                            if re.search(r"^unins|uninstall", Path(icon).name, re.I):
                                continue
                            identity = (title, icon.lower())
                            if identity in seen:
                                continue
                            seen.add(identity)
                            scanner.add(title, icon, folder=folder or str(Path(icon).parent), source="registry",
                                        reason="Installed program registry entry; DisplayIcon executable needs review")
                    except OSError as e:
                        scanner.warning("registry", e)


# Fixed script: no catalog values or user-provided names are interpolated into it.
# CreateShortcut reads existing links without invoking their targets or saving links.
SHORTCUT_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$roots = @(
    [Environment]::GetFolderPath('StartMenu'),
    [Environment]::GetFolderPath('CommonStartMenu'),
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('CommonDesktopDirectory')
) | Where-Object { $_ } | Select-Object -Unique
$shell = New-Object -ComObject WScript.Shell
$rows = [System.Collections.Generic.List[object]]::new()
$problems = [System.Collections.Generic.List[string]]::new()
foreach ($root in $roots) {
    $walkErrors = @()
    $links = Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -File -ErrorAction SilentlyContinue -ErrorVariable walkErrors
    foreach ($e in $walkErrors) { $problems.Add($e.ToString()) }
    foreach ($link in $links) {
        try {
            $shortcut = $shell.CreateShortcut($link.FullName)
            $rows.Add([pscustomobject]@{ title=$link.BaseName; path=$shortcut.TargetPath; arguments=$shortcut.Arguments; workingDirectory=$shortcut.WorkingDirectory })
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
        } catch { $problems.Add($_.ToString()) }
    }
}
[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
[pscustomobject]@{ items=@($rows.ToArray()); warnings=@($problems.ToArray()) } | ConvertTo-Json -Depth 4 -Compress
'''


def shortcuts(scanner):
    import time
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(SHORTCUT_SCRIPT.encode("utf-16-le")).decode("ascii")
    # Temporary files prevent a full pipe from deadlocking the child process.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                                    "-EncodedCommand", encoded], stdout=output, stderr=errors,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 90
        try:
            while process.poll() is None:
                scanner.check()
                if time.monotonic() > deadline:
                    raise ValueError("Shortcut discovery timed out after 90 seconds")
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        output.seek(0)
        errors.seek(0)
        if process.returncode:
            raise ValueError(errors.read().decode("utf-8", errors="replace")[:2000])
        payload = json.loads(output.read().decode("utf-8-sig"))
    for warning in payload.get("warnings", []):
        scanner.warning("shortcuts", warning)
    for item in payload.get("items", []):
        scanner.check()
        path = os.path.expandvars(item.get("path") or "")
        if Path(path).suffix.lower() not in {".exe", ".bat", ".cmd"} or not Path(path).is_file():
            continue
        if re.search(r"^unins|uninstall", Path(path).name, re.I):
            continue
        scanner.add(item["title"], path, split_args(item.get("arguments", "")),
                    str(Path(path).parent), source="shortcut",
                    working_directory=item.get("workingDirectory") or None,
                    reason="Shortcut target exists; verify application and launch arguments")


def discover(scanner):
    for name, function in [("registry", registry_apps), ("shortcuts", shortcuts), ("windows-settings", settings)]:
        try:
            function(scanner)
        except (OSError, ValueError) as e:
            scanner.warning(name, e)


def url_shortcut(scanner, path):
    import configparser
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-16" if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8-sig")
        data = configparser.ConfigParser(interpolation=None)
        data.read_string(text)
        uri = data.get("InternetShortcut", "URL", fallback="")
        if re.match(r"^[a-z][a-z0-9+.-]*:", uri, re.I):
            scanner.add(Path(path).stem, uri, source="url-shortcut",
                        reason="URL shortcut found; verify protocol handler and game installation")
    except (OSError, UnicodeError, configparser.Error) as error:
        scanner.warning("url-shortcut", f"{path}: {error}")


def settings(scanner):
    # These are built-in commands, not evidence of separately installed programs.
    for title, uri in [("Звук Windows", "ms-settings:sound"),
                       ("Микшер громкости", "ms-settings:apps-volume"),
                       ("Мышь — скорость указателя", "ms-settings:mousetouchpad"),
                       ("Экран — разрешение", "ms-settings:display"),
                       ("Экран — дополнительные параметры и герцовка", "ms-settings:display-advanced")]:
        scanner.add(title, uri, source="windows-settings", confirmed=True,
                    reason="Built-in Windows settings command; availability depends on OS policy. On Windows 11 advanced display may open the parent Display page")
    control = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/control.exe"
    if control.is_file():
        scanner.add("Мышь — свойства и параметры указателя", control, ["main.cpl"],
                    source="windows-settings", confirmed=True, reason="Built-in mouse control panel")
