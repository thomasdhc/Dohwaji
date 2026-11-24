from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from dohwaji.archive import Archive, atomic_text, cutoff_date, main
from dohwaji.server import DohwajiApp, create_server, image_names


INITIAL_CUTOFF = date(2026, 10, 23)
SECOND_CUTOFF = date(2026, 11, 15)


def tree_snapshot(root: Path) -> list[tuple[str, str, object]]:
    """Capture names and contents without following links."""
    if not root.exists():
        return []
    snapshot = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "link", os.readlink(path)))
        elif path.is_dir():
            snapshot.append((relative, "directory", None))
        else:
            snapshot.append((relative, "file", path.read_bytes()))
    return snapshot


class ArchiveFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.library = self.base / "library"
        self.state = self.base / "state"
        self.library.mkdir()
        self.archive = Archive(self.library, self.state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def image(self, name: str, content: bytes | None = None, *, folder: Path | None = None) -> Path:
        path = (folder or self.library) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else name.encode("utf-8"))
        return path

    def layout(self, relative: str = "") -> dict:
        return json.loads(self.archive.layout_path(relative).read_text(encoding="utf-8"))


class PlanningTests(ArchiveFixture):
    def test_plan_uses_only_direct_filename_dates_and_has_no_writes(self) -> None:
        eligible = self.image("Screenshot 2026-10-22 at 9.01.02 AM.png", b"eligible")
        cutoff_day = self.image("Screenshot 2026-10-23 at 1.02.03 PM.jpg", b"cutoff-day")
        self.image("Screenshot 2026-10-24 at 4.05.06 PM.webp", b"later")
        invalid = self.image("Screenshot 2026-02-30 at 1.02.03 PM.png", b"invalid-date")
        unknown = self.image("reference.png", b"unknown")
        self.image("Screenshot 2026-10-01 at 1.02.03 PM.txt", b"not-an-image")
        collection = self.library / "existing collection"
        nested = self.image(
            "Screenshot 2026-01-01 at 1.02.03 PM.png", b"nested", folder=collection
        )

        # Deliberately contradict the filename dates with modification times.
        os.utime(eligible, (2_000_000_000, 2_000_000_000))
        os.utime(cutoff_day, (1, 1))
        library_before = tree_snapshot(self.library)
        state_before = tree_snapshot(self.state)

        plan = self.archive.plan(INITIAL_CUTOFF)

        self.assertEqual(
            plan["files"],
            [{
                "name": eligible.name,
                "destination": f"archive/before-2026-10-23/{eligible.name}",
                "size": len(b"eligible"),
            }],
        )
        self.assertEqual(plan["cutoffs_after"], ["2026-10-23"])
        self.assertEqual(set(plan["skipped"]), {invalid.name, unknown.name})
        self.assertEqual(tree_snapshot(self.library), library_before)
        self.assertEqual(tree_snapshot(self.state), state_before)
        self.assertEqual(nested.read_bytes(), b"nested")
        self.assertFalse((self.library / "archive").exists())

    def test_malformed_cutoffs_are_rejected_without_writes(self) -> None:
        self.image("Screenshot 2026-10-01 at noon.png")
        before = tree_snapshot(self.library)
        for value in ("2026-2-03", "2026/02/03", "2026-02-30"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cutoff_date(value)

        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            result = main([
                str(self.library), "--before", "2026-02-30", "--state-dir", str(self.state)
            ])
        self.assertEqual(result, 2)
        self.assertIn("dohwaji archive:", stderr.getvalue())
        self.assertEqual(tree_snapshot(self.library), before)
        self.assertFalse(self.state.exists())

    def test_existing_destination_collision_is_never_overwritten(self) -> None:
        name = "Screenshot 2026-10-01 at 1.02.03 PM.png"
        source = self.image(name, b"source")
        destination = self.image(
            name, b"existing", folder=self.library / "archive" / "before-2026-10-23"
        )

        with self.assertRaisesRegex(ValueError, "Destination already exists"):
            self.archive.plan(INITIAL_CUTOFF)

        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertFalse(self.state.exists())

    def test_unmanaged_interval_folder_is_left_untouched(self) -> None:
        source = self.image("Screenshot 2026-10-01 at noon.png", b"source")
        folder = self.library / "archive" / "before-2026-10-23"
        self.image("existing.png", b"existing", folder=folder)
        before = tree_snapshot(self.library)

        with self.assertRaisesRegex(ValueError, "Unmanaged archive folder"):
            self.archive.apply(INITIAL_CUTOFF)

        self.assertEqual(tree_snapshot(self.library), before)
        self.assertEqual(source.read_bytes(), b"source")
        self.assertFalse(self.archive.history_path.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_source_and_archive_symlinks_are_rejected(self) -> None:
        outside_file = self.base / "outside.png"
        outside_file.write_bytes(b"outside")
        linked_name = "Screenshot 2026-10-01 at 1.02.03 PM.png"
        (self.library / linked_name).symlink_to(outside_file)
        with self.assertRaisesRegex(ValueError, "Refusing symbolic link"):
            self.archive.plan(INITIAL_CUTOFF)

        (self.library / linked_name).unlink()
        self.image(linked_name, b"source")
        outside_folder = self.base / "outside-folder"
        outside_folder.mkdir()
        (self.library / "archive").symlink_to(outside_folder, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Refusing symbolic link"):
            self.archive.plan(INITIAL_CUTOFF)
        self.assertEqual(list(outside_folder.iterdir()), [])


class ApplyAndUndoTests(ArchiveFixture):
    def test_apply_and_undo_preserve_bytes_names_and_all_layout_positions(self) -> None:
        moved_saved = self.image("Screenshot 2026-10-20 at 1.00.00 PM.png", b"saved-bytes")
        moved_fallback = self.image("Screenshot 2026-10-21 at 1.00.00 PM.jpg", b"fallback-bytes")
        active_saved = self.image("Screenshot 2026-10-23 at 1.00.00 PM.webp", b"active-saved")
        active_fallback = self.image("Screenshot 2026-10-24 at 1.00.00 PM.gif", b"active-fallback")
        collection = self.library / "reference" / "people"
        nested = self.image("Screenshot 2020-01-01 at noon.png", b"nested", folder=collection)

        interval = self.library / "archive" / "before-2026-10-23"
        app = DohwajiApp(self.library, self.state)
        app.save_layout({"items": {
            moved_saved.name: {"x": 101, "y": 202, "z": 7},
            active_saved.name: {"x": 303, "y": 404, "z": 8},
        }})
        app.save_layout({"items": {
            nested.name: {"x": 41, "y": 42, "z": 3},
        }}, "reference/people")
        root_layout_before = self.archive.layout_path("").read_text(encoding="utf-8")
        nested_before = tree_snapshot(collection)
        nested_layout_before = self.archive.layout_path("reference/people").read_bytes()
        original_order = image_names(self.library)

        plan = self.archive.apply(INITIAL_CUTOFF)

        self.assertEqual({entry["name"] for entry in plan["files"]}, {
            moved_saved.name, moved_fallback.name
        })
        for source, expected in ((moved_saved, b"saved-bytes"), (moved_fallback, b"fallback-bytes")):
            destination = interval / source.name
            self.assertFalse(source.exists())
            self.assertEqual(destination.name, source.name)
            self.assertEqual(destination.read_bytes(), expected)
        self.assertEqual(tree_snapshot(collection), nested_before)
        self.assertEqual(self.archive.layout_path("reference/people").read_bytes(), nested_layout_before)

        expected_fallbacks = {
            name: {"x": 64 + index % 5 * 300, "y": 64 + index // 5 * 270, "z": index + 1}
            for index, name in enumerate(original_order)
        }
        self.assertEqual(self.layout("archive/before-2026-10-23")["items"], {
            moved_saved.name: {"x": 101.0, "y": 202.0, "z": 7},
            moved_fallback.name: expected_fallbacks[moved_fallback.name],
        })
        self.assertEqual(self.layout()["items"], {
            active_saved.name: {"x": 303.0, "y": 404.0, "z": 8},
            active_fallback.name: expected_fallbacks[active_fallback.name],
        })

        self.archive.undo()

        self.assertEqual(moved_saved.read_bytes(), b"saved-bytes")
        self.assertEqual(moved_fallback.read_bytes(), b"fallback-bytes")
        self.assertEqual(self.archive.layout_path("").read_text(encoding="utf-8"), root_layout_before)
        self.assertFalse(self.archive.layout_path("archive/before-2026-10-23").exists())

    def test_archive_trims_root_blank_space_and_undo_restores_original_offset(self) -> None:
        moved = self.image("Screenshot 2026-10-20 at noon.png")
        first = self.image("Screenshot 2026-10-23 at noon.png")
        second = self.image("Screenshot 2026-10-24 at noon.png")
        unknown = self.image("reference.png")
        items = {
            moved.name: {"x": 200, "y": 8000, "z": 9},
            first.name: {"x": 900, "y": 5400.5, "z": 2},
            second.name: {"x": 300, "y": 5000.25, "z": 7},
            unknown.name: {"x": 600, "y": 5300.125, "z": 4},
        }
        app = DohwajiApp(self.library, self.state)
        app.save_layout({"items": items})
        before = self.archive.layout_path("").read_bytes()
        self.archive.plan(INITIAL_CUTOFF)
        self.assertEqual(self.archive.layout_path("").read_bytes(), before)

        self.archive.apply(INITIAL_CUTOFF)

        self.assertEqual(self.layout()["items"], {
            first.name: {"x": 900, "y": 464.25, "z": 2},
            second.name: {"x": 300, "y": 64, "z": 7},
            unknown.name: {"x": 600, "y": 363.875, "z": 4},
        })
        self.assertEqual(self.layout("archive/before-2026-10-23")["items"], {
            moved.name: items[moved.name],
        })
        self.archive.undo()
        self.assertEqual(self.archive.layout_path("").read_bytes(), before)

    def test_unsaved_remaining_images_are_translated_from_the_original_grid(self) -> None:
        for index in range(6):
            self.image(f"Screenshot 2026-10-20 at {index}.png")
        active = self.image("Screenshot 2026-10-23 at noon.png")
        self.archive.apply(INITIAL_CUTOFF)
        self.assertEqual(self.layout()["items"], {
            active.name: {"x": 364, "y": 64, "z": 7},
        })
        self.assertEqual(
            self.layout("archive/before-2026-10-23")["items"]["Screenshot 2026-10-20 at 5.png"],
            {"x": 64, "y": 334, "z": 6},
        )
        self.archive.undo()
        self.assertFalse(self.archive.layout_path("").exists())

    def test_root_at_or_above_top_margin_is_not_shifted_down(self) -> None:
        moved = self.image("Screenshot 2026-10-20 at noon.png")
        active = self.image("Screenshot 2026-10-23 at noon.png")
        app = DohwajiApp(self.library, self.state)
        for y in (0, 32.5, 64):
            with self.subTest(y=y):
                position = {"x": 123, "y": y, "z": 4}
                app.save_layout({"items": {
                    moved.name: {"x": 64, "y": 64, "z": 1}, active.name: position,
                }})
                self.archive.apply(INITIAL_CUTOFF)
                self.assertEqual(self.layout()["items"], {active.name: position})
                self.archive.undo()

    def test_archiving_every_image_leaves_an_empty_root_layout(self) -> None:
        moved = self.image("Screenshot 2026-10-20 at noon.png")
        DohwajiApp(self.library, self.state).save_layout({"items": {
            moved.name: {"x": 64, "y": 5000, "z": 1},
        }})
        self.archive.apply(INITIAL_CUTOFF)
        self.assertEqual(self.layout()["items"], {})
        self.assertEqual(self.layout("archive/before-2026-10-23")["items"][moved.name]["y"], 5000)

    def test_batch_without_moves_does_not_reposition_root(self) -> None:
        active = self.image("Screenshot 2026-11-23 at noon.png")
        DohwajiApp(self.library, self.state).save_layout({"items": {
            active.name: {"x": 200, "y": 5000, "z": 3},
        }})
        before = self.archive.layout_path("").read_bytes()
        for cutoff in (INITIAL_CUTOFF, INITIAL_CUTOFF, SECOND_CUTOFF):
            self.archive.apply(cutoff)
            self.assertEqual(self.archive.layout_path("").read_bytes(), before)

    def test_equal_and_later_cutoffs_append_to_the_correct_historical_interval(self) -> None:
        first = self.image("Screenshot 2026-10-20 at noon.png", b"first")
        self.archive.apply(INITIAL_CUTOFF)

        late = self.image("Screenshot 2026-09-01 at noon.png", b"late")
        equal_plan = self.archive.apply(INITIAL_CUTOFF)
        self.assertEqual(equal_plan["cutoffs_after"], ["2026-10-23"])
        self.assertEqual(
            equal_plan["files"][0]["destination"], f"archive/before-2026-10-23/{late.name}"
        )
        history_before_noop = self.archive.history_path.read_bytes()
        noop = self.archive.apply(INITIAL_CUTOFF)
        self.assertEqual(noop["files"], [])
        self.assertEqual(self.archive.history_path.read_bytes(), history_before_noop)

        another_old = self.image("Screenshot 2026-08-15 at noon.png", b"another-old")
        middle = self.image("Screenshot 2026-11-01 at noon.png", b"middle")
        cutoff_day = self.image("Screenshot 2026-11-15 at noon.png", b"cutoff-day")
        later_plan = self.archive.apply(SECOND_CUTOFF)
        destinations = {entry["name"]: entry["destination"] for entry in later_plan["files"]}

        self.assertEqual(
            destinations[another_old.name], f"archive/before-2026-10-23/{another_old.name}"
        )
        self.assertEqual(
            destinations[middle.name], f"archive/2026-10-23_to_2026-11-15/{middle.name}"
        )
        self.assertNotIn(cutoff_day.name, destinations)
        self.assertTrue(cutoff_day.exists())
        self.assertEqual((self.library / "archive" / "before-2026-10-23" / first.name).read_bytes(), b"first")
        self.assertEqual(self.archive.history()["cutoffs"], ["2026-10-23", "2026-11-15"])

    def test_earlier_cutoff_is_rejected_without_changing_files_or_history(self) -> None:
        self.image("Screenshot 2026-10-01 at noon.png", b"initial")
        self.archive.apply(INITIAL_CUTOFF)
        self.image("Screenshot 2026-09-01 at noon.png", b"late")
        library_before = tree_snapshot(self.library)
        state_before = tree_snapshot(self.state)

        with self.assertRaisesRegex(ValueError, "on or after 2026-10-23"):
            self.archive.apply(date(2026, 10, 22))

        self.assertEqual(tree_snapshot(self.library), library_before)
        self.assertEqual(tree_snapshot(self.state), state_before)

    def test_empty_apply_records_boundary_without_empty_folder(self) -> None:
        self.archive.apply(INITIAL_CUTOFF)
        self.assertEqual(self.archive.history()["cutoffs"], ["2026-10-23"])
        self.assertFalse((self.library / "archive").exists())
        late = self.image("Screenshot 2026-10-01 at noon.png", b"late")
        recent = self.image("Screenshot 2026-11-01 at noon.png", b"recent")
        plan = self.archive.apply(SECOND_CUTOFF)
        destinations = {entry["name"]: entry["destination"] for entry in plan["files"]}
        self.assertEqual(destinations[late.name], f"archive/before-2026-10-23/{late.name}")
        self.assertEqual(destinations[recent.name], f"archive/2026-10-23_to_2026-11-15/{recent.name}")

    def test_undo_plan_has_no_writes(self) -> None:
        self.image("Screenshot 2026-10-01 at noon.png", b"original")
        self.archive.apply(INITIAL_CUTOFF)
        library_before = tree_snapshot(self.library)
        state_before = tree_snapshot(self.state)

        history, batch = self.archive.undo_plan()

        self.assertIsNone(history["pending"])
        self.assertEqual(batch["before"], "2026-10-23")
        self.assertEqual(tree_snapshot(self.library), library_before)
        self.assertEqual(tree_snapshot(self.state), state_before)

    def test_multiple_cutoffs_can_be_undone_in_reverse_order(self) -> None:
        old = self.image("Screenshot 2026-10-01 at noon.png", b"old")
        self.archive.apply(INITIAL_CUTOFF)
        middle = self.image("Screenshot 2026-11-01 at noon.png", b"middle")
        self.archive.apply(SECOND_CUTOFF)

        second = self.archive.undo()
        self.assertEqual(second["before"], "2026-11-15")
        self.assertEqual(middle.read_bytes(), b"middle")
        self.assertFalse(old.exists())
        self.assertEqual(self.archive.history()["cutoffs"], ["2026-10-23"])

        first = self.archive.undo()
        self.assertEqual(first["before"], "2026-10-23")
        self.assertEqual(old.read_bytes(), b"old")
        self.assertEqual(middle.read_bytes(), b"middle")
        self.assertEqual(self.archive.history()["cutoffs"], [])
        self.assertEqual(self.archive.history()["batches"], [])

    def test_undo_cleanup_failure_retries_same_batch_not_previous(self) -> None:
        old = self.image("Screenshot 2026-10-01 at noon.png", b"old")
        self.archive.apply(INITIAL_CUTOFF)
        newer = self.image("Screenshot 2026-11-01 at noon.png", b"newer")
        self.archive.apply(SECOND_CUTOFF)
        original_rmdir = Path.rmdir

        def interrupt_cleanup(folder: Path) -> None:
            original_rmdir(folder)
            raise InterruptedError("cleanup interrupted")

        with mock.patch.object(Path, "rmdir", interrupt_cleanup):
            with self.assertRaises(InterruptedError):
                self.archive.undo()
        self.assertEqual(self.archive.history()["pending"]["action"], "undo")
        self.archive.undo()
        self.assertEqual(newer.read_bytes(), b"newer")
        self.assertFalse(old.exists())
        self.assertEqual(self.archive.history()["cutoffs"], ["2026-10-23"])
        self.assertEqual(len(self.archive.history()["batches"]), 1)

    def test_undo_refuses_changed_image_and_changed_layout(self) -> None:
        source = self.image("Screenshot 2026-10-01 at noon.png", b"original")
        self.archive.apply(INITIAL_CUTOFF)
        target = self.library / "archive" / "before-2026-10-23" / source.name
        target.write_bytes(b"changed")

        with self.assertRaisesRegex(ValueError, "File changed; refusing undo"):
            self.archive.undo()
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"changed")
        self.assertIsNone(self.archive.history()["pending"])

        target.write_bytes(b"original")
        target_layout = self.archive.layout_path("archive/before-2026-10-23")
        target_layout.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Layout changed; refusing undo"):
            self.archive.undo()
        self.assertFalse(source.exists())
        self.assertTrue(target.exists())
        self.assertIsNone(self.archive.history()["pending"])

    def test_active_server_blocks_archive_apply(self) -> None:
        source = self.image("Screenshot 2026-10-01 at noon.png", b"original")
        server = create_server(DohwajiApp(self.library, self.state), 0)
        try:
            with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                self.archive.apply(INITIAL_CUTOFF)
        finally:
            server.server_close()

        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse(self.archive.history_path.exists())


class RecoveryTests(ArchiveFixture):
    def prepare_recovery_case(self, label: str) -> tuple[Archive, Path, Path, str]:
        library = self.base / label / "library"
        state = self.base / label / "state"
        library.mkdir(parents=True)
        source = library / "Screenshot 2026-10-01 at noon.png"
        source.write_bytes(b"recoverable")
        (library / "Screenshot 2026-10-23 at noon.png").write_bytes(b"active")
        app = DohwajiApp(library, state)
        app.save_layout({"items": {
            source.name: {"x": 12, "y": 34, "z": 2},
        }})
        archive = Archive(library, state)
        root_layout_before = archive.layout_path("").read_text(encoding="utf-8")
        return archive, library, source, root_layout_before

    def assert_recovered(
        self, archive: Archive, library: Path, source: Path, root_layout_before: str
    ) -> None:
        destination = library / "archive" / "before-2026-10-23" / source.name
        self.assertEqual(source.read_bytes(), b"recoverable")
        self.assertFalse(destination.exists())
        self.assertEqual(archive.layout_path("").read_text(encoding="utf-8"), root_layout_before)
        self.assertFalse(archive.layout_path("archive/before-2026-10-23").exists())
        history = archive.history()
        self.assertIsNone(history["pending"])
        self.assertEqual(history["cutoffs"], [])
        self.assertEqual(history["batches"], [])

    def test_process_exit_after_link_recovers_without_prior_layout_state(self) -> None:
        source = self.image("Screenshot 2026-10-01 at noon.png", b"process-exit")
        script = """
from datetime import date
from pathlib import Path
import os
import sys
from dohwaji.archive import Archive
original_link = os.link
def exit_after_link(source, destination):
    original_link(source, destination)
    os._exit(77)
os.link = exit_after_link
Archive(Path(sys.argv[1]), Path(sys.argv[2])).apply(date(2026, 10, 23))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.library), str(self.state)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 77, result.stderr.decode())
        target = self.library / "archive" / "before-2026-10-23" / source.name
        self.assertTrue(source.samefile(target))
        self.archive.undo()
        self.assertEqual(source.read_bytes(), b"process-exit")
        self.assertFalse(target.exists())
        self.assertFalse(self.archive.layout_path("").exists())
        self.assertIsNone(self.archive.history()["pending"])

    def test_link_interruption_has_prior_journal_and_is_fully_recoverable(self) -> None:
        archive, library, source, root_layout_before = self.prepare_recovery_case("link")
        observed_pending = []

        def interrupt_link(_source: Path, _target: Path) -> None:
            observed_pending.append(archive.history()["pending"])
            raise InterruptedError("link interrupted")

        with mock.patch("dohwaji.archive.os.link", side_effect=interrupt_link):
            with self.assertRaisesRegex(InterruptedError, "link interrupted"):
                archive.apply(INITIAL_CUTOFF)

        self.assertEqual(observed_pending[0]["action"], "archive")
        self.assertTrue(source.exists())
        self.assertIsNotNone(archive.history()["pending"])
        with self.assertRaisesRegex(ValueError, "needs recovery"):
            create_server(DohwajiApp(library, archive.state_root), 0)
        archive.undo()
        self.assert_recovered(archive, library, source, root_layout_before)

    def test_link_interruption_with_both_copies_is_fully_recoverable(self) -> None:
        archive, library, source, root_layout_before = self.prepare_recovery_case("both-links")
        original_link = os.link

        def interrupt_link(source: Path, target: Path) -> None:
            original_link(source, target)
            raise InterruptedError("linked but not removed")

        with mock.patch("dohwaji.archive.os.link", side_effect=interrupt_link):
            with self.assertRaises(InterruptedError):
                archive.apply(INITIAL_CUTOFF)
        target = library / "archive" / "before-2026-10-23" / source.name
        self.assertTrue(source.samefile(target))
        archive.undo()
        self.assert_recovered(archive, library, source, root_layout_before)

    def test_undo_interruption_is_itself_recoverable(self) -> None:
        archive, library, source, root_layout_before = self.prepare_recovery_case("undo")
        archive.apply(INITIAL_CUTOFF)
        original_link = os.link

        def interrupt_link(source: Path, target: Path) -> None:
            original_link(source, target)
            raise InterruptedError("undo interrupted after link")

        with mock.patch("dohwaji.archive.os.link", side_effect=interrupt_link):
            with self.assertRaises(InterruptedError):
                archive.undo()
        self.assertEqual(archive.history()["pending"]["action"], "undo")
        archive.undo()
        self.assert_recovered(archive, library, source, root_layout_before)

    def test_unlink_interruption_after_removal_is_fully_recoverable(self) -> None:
        archive, library, source, root_layout_before = self.prepare_recovery_case("unlink")
        archive_source = archive.path(source.name)
        original_unlink = os.unlink

        def interrupt_unlink(path: object, *args: object, **kwargs: object) -> None:
            original_unlink(path, *args, **kwargs)
            if Path(path) == archive_source:
                raise InterruptedError("unlink interrupted")

        with mock.patch("dohwaji.archive.os.unlink", side_effect=interrupt_unlink):
            with self.assertRaisesRegex(InterruptedError, "unlink interrupted"):
                archive.apply(INITIAL_CUTOFF)

        destination = library / "archive" / "before-2026-10-23" / source.name
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"recoverable")
        self.assertIsNotNone(archive.history()["pending"])
        archive.undo()
        self.assert_recovered(archive, library, source, root_layout_before)

    def test_interruption_after_root_translation_restores_original_offset(self) -> None:
        moved = self.image("Screenshot 2026-10-20 at noon.png", b"moved")
        active = self.image("Screenshot 2026-10-23 at noon.png", b"active")
        app = DohwajiApp(self.library, self.state)
        app.save_layout({"items": {
            moved.name: {"x": 100, "y": 4000, "z": 2},
            active.name: {"x": 200, "y": 5000, "z": 3},
        }})
        root_path = self.archive.layout_path("")
        before = root_path.read_bytes()

        def interrupt_after_root_save(path: Path, text: str) -> None:
            atomic_text(path, text)
            if path == root_path:
                self.assertEqual(self.layout()["items"][active.name]["y"], 64)
                raise InterruptedError("root translation interrupted")

        with mock.patch("dohwaji.archive.atomic_text", side_effect=interrupt_after_root_save):
            with self.assertRaises(InterruptedError):
                self.archive.apply(INITIAL_CUTOFF)
        self.archive.undo()
        self.assertEqual(root_path.read_bytes(), before)
        self.assertEqual(moved.read_bytes(), b"moved")
        self.assertEqual(active.read_bytes(), b"active")

    def test_layout_save_interruption_after_write_is_fully_recoverable(self) -> None:
        archive, library, source, root_layout_before = self.prepare_recovery_case("layout")

        def interrupt_layout(path: Path, text: str) -> None:
            atomic_text(path, text)
            if path != archive.history_path:
                raise InterruptedError("layout save interrupted")

        with mock.patch("dohwaji.archive.atomic_text", side_effect=interrupt_layout):
            with self.assertRaisesRegex(InterruptedError, "layout save interrupted"):
                archive.apply(INITIAL_CUTOFF)

        destination = library / "archive" / "before-2026-10-23" / source.name
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"recoverable")
        self.assertIsNotNone(archive.history()["pending"])
        archive.undo()
        self.assert_recovered(archive, library, source, root_layout_before)


class CommandLineTests(ArchiveFixture):
    def test_cli_preview_apply_and_undo_lifecycle(self) -> None:
        source = self.image("Screenshot 2026-10-01 at noon.png", b"cli")
        common = [str(self.library), "--state-dir", str(self.state)]

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = main([*common, "--before", "2026-10-23"])
        self.assertEqual(result, 0)
        self.assertIn("Preview only", output.getvalue())
        self.assertEqual(source.read_bytes(), b"cli")
        self.assertFalse(self.state.exists())

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main([*common, "--before", "2026-10-23", "--apply"]), 0)
        destination = self.library / "archive" / "before-2026-10-23" / source.name
        self.assertEqual(destination.read_bytes(), b"cli")
        state_before_preview = tree_snapshot(self.state)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main([*common, "--undo"]), 0)
        self.assertEqual(tree_snapshot(self.state), state_before_preview)
        self.assertFalse(source.exists())

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main([*common, "--undo", "--apply"]), 0)
        self.assertEqual(source.read_bytes(), b"cli")
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
