import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from godji_scanner.cloud_api import CloudApiError, create_catalog_zip, list_clubs
from godji_scanner.catalog import read_catalog


class Response:
    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = {"Content-Type": content_type}
    def read(self, _limit):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False


class CloudApiTests(unittest.TestCase):
    def test_lists_clubs_from_data_wrapper(self):
        body = json.dumps({"data": [{"id": "bor", "name": "Бор"}, {"id": "bad"}]}).encode()
        with patch("godji_scanner.cloud_api.urlopen", return_value=Response(body)) as open_url:
            self.assertEqual(list_clubs("https://cloud.example", "secret"), [{"id": "bor", "name": "Бор"}])
            self.assertEqual(open_url.call_args.args[0].full_url, "https://cloud.example/api/v1/manager/clubs")

    def test_catalog_downloads_covers_and_converts_to_zip_template(self):
        catalog = {"data": {"categories": [], "items": [{"id": "cs", "title": "CS2", "type": "game",
                   "path": "C:/Steam/steam.exe", "args": ["-applaunch", "730"],
                   "coverUrl": "http://cloud.example/catalog/cs.jpg"}]}}
        calls = []
        def open_url(request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith("/catalog"):
                return Response(json.dumps(catalog).encode())
            return Response(b"image", "image/jpeg")
        with tempfile.TemporaryDirectory() as temp, patch("godji_scanner.cloud_api.urlopen", side_effect=open_url):
            target = Path(temp) / "catalog.zip"
            create_catalog_zip("https://cloud.example", "secret", {"id": "bor", "name": "Бор"}, target)
            data = read_catalog(target)
            self.assertEqual(data["clubId"], "bor")
            self.assertEqual(data["clubName"], "Бор")
            self.assertEqual(data["items"][0]["coverFile"], "covers/cloud-0.jpg")
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.read("covers/cloud-0.jpg"), b"image")
        self.assertEqual(calls[1], "https://cloud.example/catalog/cs.jpg")

    def test_catalog_rejects_third_party_cover(self):
        catalog = {"data": {"items": [{"id": "cs", "title": "CS2", "coverUrl": "https://example.org/cs.jpg"}]}}
        with tempfile.TemporaryDirectory() as temp, patch("godji_scanner.cloud_api.urlopen", return_value=Response(json.dumps(catalog).encode())):
            with self.assertRaises(CloudApiError):
                create_catalog_zip("https://cloud.example", "secret", {"id": "bor", "name": "Бор"}, Path(temp) / "out.zip")


if __name__ == "__main__":
    unittest.main()
