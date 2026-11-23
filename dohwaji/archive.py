"""Manually archive loose screenshots into user-selected date intervals."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
import uuid

from .locking import StateLock
from .server import DohwajiApp, LayoutStore, image_names, normalize_layout

SCREENSHOT = re.compile(r"Screenshot (\d{4}-\d{2}-\d{2}) at .+\.[^.]+")


def cutoff_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a cutoff in YYYY-MM-DD format")
    return date.fromisoformat(value)


def capture_date(name: str) -> date | None:
    match = SCREENSHOT.fullmatch(name)
    if match is None:
        return None
    try:
        return cutoff_date(match[1])
    except ValueError:
        return None


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through the standard library.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def make_directory(path: Path) -> None:
    if not path.exists():
        make_directory(path.parent)
        path.mkdir(exist_ok=True)
        sync_directory(path.parent)


def atomic_text(path: Path, text: str) -> None:
    make_directory(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".archive-", delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class Archive:
    def __init__(self, folder: Path, state_root: Path | None = None) -> None:
        app = DohwajiApp(folder, state_root)
        self.root = app.folder
        self.state_root = app.state_root
        key = hashlib.sha256(os.fsencode(str(self.root))).hexdigest()
        self.history_path = self.state_root / "archives" / key / "history.json"

    def history(self) -> dict[str, Any]:
        if not self.history_path.exists():
            return {"version": 1, "library": str(self.root), "cutoffs": [],
                    "batches": [], "pending": None}
        history = json.loads(self.history_path.read_text(encoding="utf-8"))
        if history.get("version") != 1 or history.get("library") != str(self.root):
            raise ValueError("Archive history does not match this library")
        return history

    def save_history(self, history: dict[str, Any]) -> None:
        atomic_text(self.history_path, json_text(history))

    def path(self, relative: str) -> Path:
        """Reject linked components, traversal, and non-directory parents."""
        requested = Path(relative)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError(f"Unsafe archive path: {relative}")
        current = self.root
        for index, part in enumerate(requested.parts):
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Refusing symbolic link: {current}")
            if index < len(requested.parts) - 1 and current.exists() and not current.is_dir():
                raise ValueError(f"Not a directory: {current}")
        return current

    def plan(self, before: date) -> dict[str, Any]:
        history = self.history()
        if history["pending"] is not None:
            raise ValueError("An interrupted operation needs recovery: use --undo --apply first")
        cutoffs = history["cutoffs"][:]
        cutoff = before.isoformat()
        if cutoffs and cutoff < cutoffs[-1]:
            raise ValueError(f"Cutoff must be on or after {cutoffs[-1]}")
        if not cutoffs or cutoff != cutoffs[-1]:
            cutoffs.append(cutoff)

        managed_folders = {
            str(Path(entry["destination"]).parent)
            for batch in history["batches"] for entry in batch["files"]
        }
        files = []
        skipped = []
        for name in image_names(self.root):
            captured = capture_date(name)
            if captured is None:
                skipped.append(name)
                continue
            if captured >= before:
                continue
            # Late screenshots belong to their original interval, not the newest batch.
            end_index = next(i for i, end in enumerate(cutoffs) if captured.isoformat() < end)
            end = cutoffs[end_index]
            interval = (f"before-{end}" if end_index == 0
                        else f"{cutoffs[end_index - 1]}_to_{end}")
            destination = f"archive/{interval}/{name}"
            source = self.path(name)
            target = self.path(destination)
            if target.exists():
                raise ValueError(f"Destination already exists: {target}")
            relative_folder = str(Path(destination).parent)
            if target.parent.exists() and relative_folder not in managed_folders:
                raise ValueError(f"Unmanaged archive folder already exists: {target.parent}")
            files.append({"name": name, "destination": destination, "size": source.stat().st_size})
        return {"before": cutoff, "cutoffs_before": history["cutoffs"],
                "cutoffs_after": cutoffs, "files": files, "skipped": skipped}

    def layout_path(self, relative: str) -> Path:
        return LayoutStore(self.state_root, self.path(relative)).path

    def layout_snapshot(self, relative: str) -> tuple[str | None, dict[str, Any]]:
        path = self.layout_path(relative)
        before = path.read_text(encoding="utf-8") if path.exists() else None
        # Refuse malformed saved state rather than silently losing an arrangement.
        value = json.loads(before) if before is not None else {"items": {}}
        if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
            raise ValueError(f"Invalid saved layout: {path}")
        folder = self.path(relative)
        names = image_names(folder) if folder.exists() else []
        positions = normalize_layout(value, set(names))["items"]
        # Match app.js initialPosition for images that have never been saved.
        for index, name in enumerate(names):
            positions.setdefault(name, {"x": 64 + index % 5 * 300,
                                        "y": 64 + index // 5 * 270, "z": index + 1})
        return before, positions

    def prepare_layouts(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        root_before, positions = self.layout_snapshot("")
        moved_names = {entry["name"] for entry in files}
        folders = sorted({str(Path(entry["destination"]).parent) for entry in files})
        snapshots = []
        for relative in folders:
            before, items = self.layout_snapshot(relative)
            for entry in files:
                if str(Path(entry["destination"]).parent) == relative:
                    items[entry["name"]] = positions[entry["name"]]
            after = json_text({"version": 1, "folder": str(self.path(relative)), "items": items})
            snapshots.append({"folder": relative, "before": before, "after": after})
        if files:
            active = {name: position for name, position in positions.items() if name not in moved_names}
            after = json_text({"version": 1, "folder": str(self.root), "items": active})
            snapshots.append({"folder": "", "before": root_before, "after": after})
        return snapshots

    def apply(self, before: date) -> dict[str, Any]:
        with StateLock(self.state_root):
            plan = self.plan(before)
            history = self.history()
            if not plan["files"] and plan["cutoffs_before"] == plan["cutoffs_after"]:
                return plan
            batch = {**plan, "id": uuid.uuid4().hex,
                     "layouts": self.prepare_layouts(plan["files"])}
            for entry in batch["files"]:
                entry["sha256"] = checksum(self.path(entry["name"]))
            # The full recovery journal is durable before the first image changes.
            history["pending"] = {"action": "archive", "batch": batch}
            self.save_history(history)
            for entry in batch["files"]:
                source = self.path(entry["name"])
                target = self.path(entry["destination"])
                make_directory(target.parent)
                if checksum(source) != entry["sha256"]:
                    raise ValueError(f"Source changed: {source}; use --undo --apply")
                # Same-filesystem, no-clobber move. Interruption can leave two links,
                # which undo recognizes; unlike replace/rename this never overwrites.
                os.link(source, target)
                if checksum(target) != entry["sha256"] or not source.samefile(target):
                    raise ValueError(f"Verification failed: {target}; use --undo --apply")
                source.unlink()
                sync_directory(target.parent)
                sync_directory(source.parent)
            for layout in batch["layouts"]:
                atomic_text(self.layout_path(layout["folder"]), layout["after"])
            history["cutoffs"] = plan["cutoffs_after"]
            history["batches"].append(batch)
            history["pending"] = None
            self.save_history(history)
            return plan

    def undo_plan(self) -> tuple[dict[str, Any], dict[str, Any]]:
        history = self.history()
        pending = history["pending"]
        if pending is not None:
            batch = pending["batch"]
        elif history["batches"]:
            batch = history["batches"][-1]
        else:
            raise ValueError("No archive operation to undo")
        # Preflight the entire reversal before modifying anything.
        for entry in batch["files"]:
            source = self.path(entry["name"])
            target = self.path(entry["destination"])
            if not source.exists() and not target.exists():
                raise ValueError(f"Both copies are missing: {entry['name']}")
            if source.exists() and target.exists() and not source.samefile(target):
                raise ValueError(f"Undo would overwrite an existing file: {source}")
            for path in (source, target):
                if path.exists() and checksum(path) != entry["sha256"]:
                    raise ValueError(f"File changed; refusing undo: {path}")
        for layout in batch["layouts"]:
            path = self.layout_path(layout["folder"])
            current = path.read_text(encoding="utf-8") if path.exists() else None
            allowed = (layout["before"], layout["after"]) if pending else (layout["after"],)
            if current not in allowed:
                raise ValueError(f"Layout changed; refusing undo: {path}")
        return history, batch

    def undo(self) -> dict[str, Any]:
        with StateLock(self.state_root):
            history, batch = self.undo_plan()
            if history["pending"] is None:
                history["batches"].pop()
            history["pending"] = {"action": "undo", "batch": batch}
            self.save_history(history)
            for entry in reversed(batch["files"]):
                source = self.path(entry["name"])
                target = self.path(entry["destination"])
                if target.exists():
                    if not source.exists():
                        os.link(target, source)
                    target.unlink()
                    sync_directory(source.parent)
                    sync_directory(target.parent)
            for layout in batch["layouts"]:
                path = self.layout_path(layout["folder"])
                if layout["before"] is None:
                    if path.exists():
                        path.unlink()
                        sync_directory(path.parent)
                else:
                    atomic_text(path, layout["before"])
            # Keep the undo journal pending through cleanup, so any cleanup error
            # retries this batch rather than selecting the preceding archive.
            # Remove only empty interval folders; never remove existing collections.
            for relative in {str(Path(entry["destination"]).parent) for entry in batch["files"]}:
                folder = self.path(relative)
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
                    sync_directory(folder.parent)
            history["cutoffs"] = batch["cutoffs_before"]
            history["pending"] = None
            self.save_history(history)
            return batch


def describe(plan: dict[str, Any], *, undo: bool = False) -> None:
    verb = "Restore" if undo else "Archive"
    print(f"{verb} {len(plan['files'])} screenshot(s); exclusive cutoff {plan['before']}")
    for folder, count in sorted(Counter(str(Path(f["destination"]).parent)
                                       for f in plan["files"]).items()):
        print(f"  {folder}/: {count}")
    for entry in plan["files"]:
        print(f"  {entry['name']} -> {entry['destination']}")
    for name in plan["skipped"]:
        print(f"  Skipped (unrecognized capture date): {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="library root (only loose screenshots are eligible)")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--before", metavar="YYYY-MM-DD", help="exclusive capture-date cutoff")
    action.add_argument("--undo", action="store_true", help="undo the last batch or interrupted operation")
    parser.add_argument("--apply", action="store_true", help="perform changes (default: preview only)")
    parser.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        archive = Archive(args.folder, args.state_dir)
        if args.undo:
            plan = archive.undo() if args.apply else archive.undo_plan()[1]
        else:
            before = cutoff_date(args.before)
            plan = archive.apply(before) if args.apply else archive.plan(before)
        describe(plan, undo=args.undo)
        print("Completed." if args.apply else "Preview only; add --apply to perform these changes.")
        if args.apply:
            print(f"Recovery history: {archive.history_path}")
        return 0
    except (OSError, ValueError) as error:
        print(f"dohwaji archive: {error}", file=sys.stderr)
        print("If interrupted, preview recovery with --undo before applying it.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
