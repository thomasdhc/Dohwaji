"""Dependency-free local server for the Dohwaji prototype."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit
import webbrowser

SUPPORTED_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_LAYOUT_BYTES = 2 * 1024 * 1024
STATIC_ROOT = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def default_state_root() -> Path:
    """Return an OS-appropriate directory for Dohwaji's private local state."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Dohwaji"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Dohwaji"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "dohwaji"


def image_names(folder: Path) -> list[str]:
    """List supported direct-child images in deterministic name order."""
    return sorted(
        (
            entry.name
            for entry in folder.iterdir()
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=str.casefold,
    )


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_layout(value: Any, allowed_names: set[str]) -> dict[str, Any]:
    """Validate untrusted layout JSON and keep entries for current source images."""
    normalized: dict[str, Any] = {"version": 1, "items": {}}
    if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
        return normalized

    for name, position in value["items"].items():
        if name not in allowed_names or not isinstance(position, dict):
            continue
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        if not all(_finite_number(number) for number in (x, y, z)):
            continue
        if x < 0 or y < 0 or z < 1 or x > 10_000_000 or y > 10_000_000 or z > 10_000_000:
            continue
        normalized["items"][name] = {
            "x": round(float(x), 3),
            "y": round(float(y), 3),
            "z": int(round(float(z))),
        }
    return normalized


class LayoutStore:
    """Persist canvas state outside the selected source folder."""

    def __init__(self, state_root: Path, folder: Path) -> None:
        digest = hashlib.sha256(os.fsencode(str(folder))).hexdigest()
        self.path = state_root / "layouts" / f"{digest}.json"
        self.folder = folder

    def load(self, allowed_names: set[str]) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"version": 1, "items": {}}
        return normalize_layout(value, allowed_names)

    def save(self, value: Any, allowed_names: set[str]) -> dict[str, Any]:
        normalized = normalize_layout(value, allowed_names)
        document = {
            "version": normalized["version"],
            "folder": str(self.folder),
            "items": normalized["items"],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                json.dump(document, temporary, ensure_ascii=False, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return normalized


class DohwajiApp:
    def __init__(self, folder: Path, state_root: Path | None = None) -> None:
        resolved = folder.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Not a readable folder: {folder}")

        resolved_state_root = (state_root or default_state_root()).expanduser().resolve()
        try:
            resolved_state_root.relative_to(resolved)
        except ValueError:
            pass
        else:
            raise ValueError("Dohwaji state must be stored outside the selected folder")

        self.folder = resolved
        self.state_root = resolved_state_root
        self.store = LayoutStore(resolved_state_root, resolved)

    def resolve_folder(self, relative_folder: str = "") -> Path | None:
        """Resolve a browser-supplied folder while confining it to the launched root."""
        if not isinstance(relative_folder, str):
            return None
        requested = Path(relative_folder)
        if requested.is_absolute() or ".." in requested.parts:
            return None
        try:
            resolved = (self.folder / requested).resolve()
            resolved.relative_to(self.folder)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_dir() else None

    def folder_options(self) -> list[dict[str, str]]:
        """List navigable descendant folders without traversing hidden or linked trees."""
        options = [{"value": "", "label": f"{self.folder.name or self.folder} (root)"}]
        relative_folders = []
        for current, directory_names, _ in os.walk(self.folder, followlinks=False):
            current_path = Path(current)
            directory_names[:] = [
                name
                for name in directory_names
                if not name.startswith(".") and not (current_path / name).is_symlink()
            ]
            relative_folders.extend(
                (current_path / name).relative_to(self.folder).as_posix() for name in directory_names
            )
        options.extend(
            {"value": relative, "label": relative.replace("/", " / ")}
            for relative in sorted(relative_folders, key=str.casefold)
        )
        return options

    def current_names(self, relative_folder: str = "") -> list[str]:
        folder = self.resolve_folder(relative_folder)
        if folder is None:
            raise ValueError("Folder not found")
        return image_names(folder)

    def bootstrap(self, relative_folder: str = "") -> dict[str, Any]:
        folder = self.resolve_folder(relative_folder)
        if folder is None:
            raise ValueError("Folder not found")
        names = image_names(folder)
        selected_folder = "" if folder == self.folder else folder.relative_to(self.folder).as_posix()
        store = LayoutStore(self.state_root, folder)
        return {
            "folder": folder.name or str(folder),
            "folderPath": str(folder),
            "selectedFolder": selected_folder,
            "folders": self.folder_options(),
            "images": [{"name": name} for name in names],
            "layout": store.load(set(names)),
        }

    def save_layout(self, value: Any, relative_folder: str = "") -> dict[str, Any]:
        folder = self.resolve_folder(relative_folder)
        if folder is None:
            raise ValueError("Folder not found")
        return LayoutStore(self.state_root, folder).save(value, set(image_names(folder)))

    def media_path(self, name: str, relative_folder: str = "") -> Path | None:
        folder = self.resolve_folder(relative_folder)
        if folder is None or name not in set(image_names(folder)):
            return None
        path = folder / name
        return path if path.is_file() else None


class DohwajiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: DohwajiApp) -> None:
        self.app = app
        super().__init__(address, DohwajiRequestHandler)


class DohwajiRequestHandler(BaseHTTPRequestHandler):
    server: DohwajiServer

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; ",
        )
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if parsed.path in STATIC_FILES:
            filename, content_type = STATIC_FILES[parsed.path]
            self._send_file(STATIC_ROOT / filename, content_type)
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/api/bootstrap":
            folders = query.get("folder", [""])
            if len(folders) != 1:
                self._send_error(HTTPStatus.BAD_REQUEST, "One folder is required")
                return
            try:
                bootstrap = self.server.app.bootstrap(folders[0])
            except ValueError:
                self._send_error(HTTPStatus.NOT_FOUND, "Folder not found")
                return
            self._send_json(bootstrap)
            return
        if parsed.path == "/media":
            names = query.get("name", [])
            folders = query.get("folder", [""])
            if len(names) != 1 or len(folders) != 1:
                self._send_error(HTTPStatus.BAD_REQUEST, "One folder and image name are required")
                return
            media_path = self.server.app.media_path(names[0], folders[0])
            if media_path is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Image not found")
                return
            content_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
            self._send_file(media_path, content_type)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if parsed.path != "/api/layout":
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        folders = parse_qs(parsed.query, keep_blank_values=True).get("folder", [""])
        if len(folders) != 1:
            self._send_error(HTTPStatus.BAD_REQUEST, "One folder is required")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid content length")
            return
        if content_length <= 0 or content_length > MAX_LAYOUT_BYTES:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Invalid layout size")
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
            return

        try:
            saved = self.server.app.save_layout(payload, folders[0])
        except ValueError:
            self._send_error(HTTPStatus.NOT_FOUND, "Folder not found")
            return
        self._send_json(saved)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            size = path.stat().st_size
            file = path.open("rb")
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        with file:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while chunk := file.read(1024 * 256):
                self.wfile.write(chunk)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)


def create_server(app: DohwajiApp, port: int = 8765) -> DohwajiServer:
    """Create a loopback-only server. Port zero requests an available test port."""
    return DohwajiServer(("127.0.0.1", port), app)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m dohwaji",
        description="Open a local image folder tree as persistent spatial canvases.",
    )
    parser.add_argument("folder", type=Path, help="root folder to open and navigate")
    parser.add_argument("--port", type=int, default=8765, help="local port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the default browser")
    parser.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = DohwajiApp(args.folder, args.state_dir)
        server = create_server(app, args.port)
    except (OSError, ValueError) as error:
        print(f"dohwaji: {error}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Dohwaji is showing: {app.folder}")
    print(f"Open {url}")
    print("Press Control-C to stop.")

    if not args.no_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Dohwaji.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
