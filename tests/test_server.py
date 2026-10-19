from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dohwaji.server import DohwajiApp, create_server, image_names, normalize_layout


class FolderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "references"
        self.state = self.root / "state"
        self.source.mkdir()
        (self.source / "alpha.png").write_bytes(b"png-one")
        (self.source / "Beta.JPG").write_bytes(b"jpeg-two")
        (self.source / "notes.txt").write_text("not an image", encoding="utf-8")
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "hidden.webp").write_bytes(b"nested-image")
        deeper = nested / "deeper"
        deeper.mkdir()
        (deeper / "deep.gif").write_bytes(b"deep-image")
        hidden_folder = self.source / ".hidden"
        hidden_folder.mkdir()
        (hidden_folder / "secret.png").write_bytes(b"secret-image")

    def tearDown(self) -> None:
        self.temporary.cleanup()


class ImageDiscoveryTests(FolderFixture):
    def test_lists_only_supported_direct_children_in_name_order(self) -> None:
        self.assertEqual(image_names(self.source), ["alpha.png", "Beta.JPG"])

    def test_lists_navigable_folders_without_hidden_directories(self) -> None:
        app = DohwajiApp(self.source, self.state)

        self.assertEqual(
            app.folder_options(),
            [
                {"value": "", "label": "references (root)"},
                {"value": "nested", "label": "nested"},
                {"value": "nested/deeper", "label": "nested / deeper"},
            ],
        )

    def test_normalizes_only_current_finite_positions(self) -> None:
        layout = normalize_layout(
            {
                "items": {
                    "alpha.png": {"x": 12.5, "y": 20, "z": 3},
                    "missing.png": {"x": 1, "y": 2, "z": 3},
                    "Beta.JPG": {"x": float("inf"), "y": 2, "z": 3},
                }
            },
            {"alpha.png", "Beta.JPG"},
        )
        self.assertEqual(layout["items"], {"alpha.png": {"x": 12.5, "y": 20.0, "z": 3}})


class PersistenceTests(FolderFixture):
    def test_layout_is_restored_without_modifying_source_folder(self) -> None:
        app = DohwajiApp(self.source, self.state)
        before = {
            path.relative_to(self.source): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.source.rglob("*")
        }
        payload = {
            "version": 1,
            "items": {
                "alpha.png": {"x": 101, "y": 202, "z": 7},
                "Beta.JPG": {"x": 303, "y": 404, "z": 8},
            },
        }

        app.save_layout(payload)
        reopened = DohwajiApp(self.source, self.state).bootstrap()
        after = {
            path.relative_to(self.source): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.source.rglob("*")
        }

        self.assertEqual(reopened["layout"]["items"], payload["items"])
        self.assertEqual(before, after)
        self.assertFalse(any(path.name.startswith(".dohwaji") for path in self.source.iterdir()))
        self.assertTrue(app.store.path.is_file())
        with self.assertRaises(ValueError):
            app.store.path.relative_to(self.source)

    def test_source_changes_do_not_corrupt_saved_layout(self) -> None:
        app = DohwajiApp(self.source, self.state)
        app.save_layout({"items": {"alpha.png": {"x": 1, "y": 2, "z": 3}}})
        (self.source / "alpha.png").unlink()
        (self.source / "new.webp").write_bytes(b"new")

        reopened = app.bootstrap()

        self.assertEqual([item["name"] for item in reopened["images"]], ["Beta.JPG", "new.webp"])
        self.assertEqual(reopened["layout"]["items"], {})

    def test_nested_folders_have_independent_layouts(self) -> None:
        app = DohwajiApp(self.source, self.state)
        nested_layout = {"items": {"hidden.webp": {"x": 8, "y": 9, "z": 2}}}

        app.save_layout(nested_layout, "nested")

        nested = app.bootstrap("nested")
        self.assertEqual(nested["selectedFolder"], "nested")
        self.assertEqual([item["name"] for item in nested["images"]], ["hidden.webp"])
        self.assertEqual(nested["layout"]["items"], nested_layout["items"])
        self.assertEqual(app.bootstrap()["layout"]["items"], {})

    def test_rejects_folders_outside_the_launched_root(self) -> None:
        app = DohwajiApp(self.source, self.state)

        self.assertIsNone(app.resolve_folder(".."))
        self.assertIsNone(app.resolve_folder(str(self.root)))
        with self.assertRaisesRegex(ValueError, "Folder not found"):
            app.bootstrap("../references")

    def test_rejects_state_storage_inside_the_source_folder(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the selected folder"):
            DohwajiApp(self.source, self.source / ".dohwaji")


class HttpTests(FolderFixture):
    def setUp(self) -> None:
        super().setUp()
        self.app = DohwajiApp(self.source, self.state)
        self.server = create_server(self.app, 0)
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def get_json(self, path: str) -> dict:
        with urlopen(self.base + path) as response:
            return json.load(response)

    def test_bootstrap_and_media_are_served(self) -> None:
        bootstrap = self.get_json("/api/bootstrap")
        self.assertEqual([item["name"] for item in bootstrap["images"]], ["alpha.png", "Beta.JPG"])

        query = urlencode({"name": "alpha.png"})
        with urlopen(f"{self.base}/media?{query}") as response:
            self.assertEqual(response.read(), b"png-one")
            self.assertEqual(response.headers.get_content_type(), "image/png")

    def test_nested_folder_bootstrap_media_and_layout(self) -> None:
        folder_query = urlencode({"folder": "nested"})
        bootstrap = self.get_json(f"/api/bootstrap?{folder_query}")
        self.assertEqual(bootstrap["selectedFolder"], "nested")
        self.assertEqual([item["name"] for item in bootstrap["images"]], ["hidden.webp"])
        self.assertEqual(
            [folder["value"] for folder in bootstrap["folders"]],
            ["", "nested", "nested/deeper"],
        )

        media_query = urlencode({"folder": "nested", "name": "hidden.webp"})
        with urlopen(f"{self.base}/media?{media_query}") as response:
            self.assertEqual(response.read(), b"nested-image")

        payload = {"items": {"hidden.webp": {"x": 31, "y": 42, "z": 3}}}
        request = Request(
            f"{self.base}/api/layout?{folder_query}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            self.assertEqual(json.load(response)["items"], payload["items"])
        self.assertEqual(self.get_json(f"/api/bootstrap?{folder_query}")["layout"]["items"], payload["items"])
        self.assertEqual(self.get_json("/api/bootstrap")["layout"]["items"], {})

    def test_layout_post_persists_and_filters_unknown_images(self) -> None:
        payload = {
            "items": {
                "alpha.png": {"x": 11, "y": 22, "z": 2},
                "unknown.png": {"x": 1, "y": 1, "z": 1},
            }
        }
        request = Request(
            self.base + "/api/layout",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            saved = json.load(response)

        self.assertEqual(saved["items"], {"alpha.png": {"x": 11.0, "y": 22.0, "z": 2}})
        self.assertEqual(self.get_json("/api/bootstrap")["layout"], saved)

    def test_media_endpoint_rejects_nested_paths(self) -> None:
        query = urlencode({"name": "nested/hidden.webp"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base}/media?{query}")
        self.assertEqual(caught.exception.code, 404)

    def test_http_endpoints_reject_folder_traversal(self) -> None:
        folder_query = urlencode({"folder": ".."})
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base}/api/bootstrap?{folder_query}")
        self.assertEqual(caught.exception.code, 404)

        media_query = urlencode({"folder": "..", "name": "outside.png"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base}/media?{media_query}")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
