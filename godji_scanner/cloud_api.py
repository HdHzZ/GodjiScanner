"""Read-only client for the documented Godji Cloud Manager API."""

import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import zipfile

MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_COVER_BYTES = 20 * 1024 * 1024
MAX_TOTAL_COVER_BYTES = 512 * 1024 * 1024


class CloudApiError(RuntimeError):
    pass


def _base_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Cloud URL must use https, for example https://cloud.godjios.ru")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _request(url, token=None, limit=MAX_CATALOG_BYTES):
    headers = {"Accept": "application/json"}
    if token is not None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Enter a Manager API key")
        headers["Authorization"] = "Bearer " + token.strip()
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise CloudApiError("Cloud response exceeds the allowed size")
            return data, response.headers.get("Content-Type", "")
    except CloudApiError:
        raise
    except Exception as error:
        raise CloudApiError("Cloud request failed: " + str(error)) from error


def _json(url, token):
    raw, _ = _request(url, token)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CloudApiError("Cloud returned invalid JSON") from error
    if not isinstance(data, dict) or "data" not in data:
        raise CloudApiError("Cloud response has no data field")
    return data["data"]


def list_clubs(base_url, token):
    """Return the clubs visible to the supplied Manager API key."""
    base = _base_url(base_url)
    raw = _json(base + "/api/v1/manager/clubs", token)
    if not isinstance(raw, list):
        raise CloudApiError("Club list has an unexpected format")
    clubs = []
    for club in raw:
        if isinstance(club, dict) and isinstance(club.get("id"), str) and isinstance(club.get("name"), str):
            clubs.append({"id": club["id"], "name": club["name"]})
    return sorted(clubs, key=lambda item: item["name"].casefold())


def get_catalog(base_url, token, club_id):
    base = _base_url(base_url)
    if not isinstance(club_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", club_id):
        raise ValueError("Invalid club ID")
    raw = _json(base + "/api/v1/manager/clubs/" + club_id + "/catalog", token)
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise CloudApiError("Catalog has an unexpected format")
    return raw


def _safe_cover_url(base_url, cover_url):
    if not isinstance(cover_url, str):
        raise CloudApiError("Catalog contains an invalid cover URL")
    base = urlsplit(_base_url(base_url))
    target = urlsplit(urljoin(_base_url(base_url) + "/", cover_url))
    # Covers are expected from the same Cloud host. Upgrade the older http URLs
    # returned by current catalog exports before requesting them.
    if target.hostname != base.hostname or target.username or target.password or target.port not in (None, 443, 80):
        raise CloudApiError("Catalog cover points outside the selected Cloud")
    if target.scheme not in ("http", "https"):
        raise CloudApiError("Catalog cover URL has an unsupported scheme")
    return urlunsplit(("https", base.netloc, target.path, target.query, ""))


def _cover_extension(url, content_type):
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".ico"}:
        return suffix
    content_type = content_type.lower()
    return ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".jpg"


def create_catalog_zip(base_url, token, club, destination):
    """Download one catalog and its same-host covers into a normal v3 ZIP template."""
    if not isinstance(club, dict) or not isinstance(club.get("id"), str):
        raise ValueError("Club record is invalid")
    catalog = get_catalog(base_url, token, club["id"])
    catalog = json.loads(json.dumps(catalog))  # do not mutate an API response owned by caller
    catalog["version"] = 3
    catalog["clubId"] = club["id"]
    catalog["clubName"] = club.get("name", club["id"])
    covers = []
    total = 0
    for index, item in enumerate(catalog["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
            raise CloudApiError("Catalog contains an invalid application")
        cover_url = item.get("coverUrl")
        if not cover_url:
            continue
        url = _safe_cover_url(base_url, cover_url)
        payload, content_type = _request(url, None, MAX_COVER_BYTES)
        total += len(payload)
        if total > MAX_TOTAL_COVER_BYTES:
            raise CloudApiError("Catalog covers exceed the total size limit")
        name = f"covers/cloud-{index}{_cover_extension(url, content_type)}"
        item["coverFile"] = name
        covers.append((name, payload))
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in covers:
            archive.writestr(name, payload)
        archive.writestr("catalog.json", json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"))
    return target
