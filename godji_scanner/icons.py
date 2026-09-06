"""Extract local Windows icons to PNG; no downloads or discovered program launches."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$request = Get-Content -LiteralPath $env:GODJI_ICON_REQUEST -Raw -Encoding UTF8 | ConvertFrom-Json
$results = @()
foreach ($entry in $request) {
    $icon = $null
    $bitmap = $null
    $fallback = $false
    try {
        if ($entry.source -and (Test-Path -LiteralPath $entry.source -PathType Leaf)) {
            try { $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($entry.source) } catch {}
        }
        if (-not $icon) { $icon = [System.Drawing.SystemIcons]::Application.Clone(); $fallback = $true }
        $bitmap = $icon.ToBitmap()
        $bitmap.Save($entry.output, [System.Drawing.Imaging.ImageFormat]::Png)
        $results += [pscustomobject]@{ id=$entry.id; fallback=$fallback }
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        if ($icon) { $icon.Dispose() }
    }
}
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
ConvertTo-Json -InputObject @($results) -Compress
'''


def extract_icons(result, folder):
    if os.name != "nt":
        return
    folder = Path(folder)
    icons = folder / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    selected = [i for i in result["items"] if i.get("displayGroup") == "applications"][:1000]
    requests = []
    for index, item in enumerate(selected):
        # Use locally existing icon resources from discovery, never remote assets.
        source = item.get("iconSource") or item["launch"]["path"]
        if source.startswith("\\\\") or "://" in source or source.startswith("ms-settings:"):
            source = ""
        filename = f"icon-{index}.png"
        requests.append({"id": item["id"], "source": source, "output": str((icons / filename).resolve())})
    if not requests:
        return
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with tempfile.TemporaryDirectory(prefix="godji-icons-") as temp:
        request = Path(temp) / "request.json"
        request.write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, GODJI_ICON_REQUEST=str(request))
        process = subprocess.run([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                  base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")],
                                 capture_output=True, env=env, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode:
            raise ValueError("Local icon extraction failed")
        values = json.loads(process.stdout.decode("utf-8-sig"))
    by_id = {i["id"]: i for i in selected}
    for value, request in zip(values, requests):
        item = by_id[value["id"]]
        item["iconFile"] = "icons/" + Path(request["output"]).name
        item["iconOrigin"] = "placeholder" if value["fallback"] else "local-executable"
