# Dohwaji

Dohwaji is a local-first spatial canvas for viewing and arranging drawing references.

The project is inspired by using the macOS Desktop as a visual worktable: related screenshots remain visible together, their placement carries meaning, and opening a folder should not be required to compare them.

## First prototype

The first prototype will open one local folder as a desktop-like canvas and allow the user to:

- see the folder's images together without importing or copying them;
- select one image or marquee-select several images;
- drag selected images into a useful spatial arrangement;
- reopen the folder later with that arrangement restored; and
- open an original image directly from the canvas.

The original image files must remain unchanged. Layout data will be stored separately by Dohwaji.

See [`docs/PLAN.md`](docs/PLAN.md) for the prototype boundary and acceptance criteria.

## Launch Dohwaji

Dohwaji has no third-party dependencies and requires Python 3.8 or newer.

1. Open Terminal.
2. Change to the Dohwaji repository:

   ```bash
   cd /Users/donghyukchang/Workspace/dohwaji
   ```

3. Launch the main library canvas, which contains loose and newly captured screenshots:

   ```bash
   python3 -m dohwaji library
   ```

Dohwaji opens `http://127.0.0.1:8765/` in the default browser. Keep the Terminal process running while using the canvas. Press Control-C in that Terminal window to stop Dohwaji.

The local image collection lives under `library/`, which is intentionally excluded from Git. macOS also saves new screenshots directly into this directory. Existing reference collections remain nested beneath it; cutoff-based batches live under `archive/`.

Use the folder menu in the top bar to switch between the library root and any nested archive. Each selected folder becomes its own canvas, shows only images directly inside it, and restores its own saved arrangement.

You can also launch a nested folder directly. Quote paths containing spaces:

```bash
python3 -m dohwaji library/reference/fashion
python3 -m dohwaji "library/screenshots/misc/art"
```

If the browser does not open automatically, visit `http://127.0.0.1:8765/` manually. If port 8765 is already occupied, select another local port:

```bash
python3 -m dohwaji library --port 8766
```

On the canvas:

- choose the active folder from the menu beside **Dohwaji**;
- drag across empty canvas space to select several images;
- drag any selected image to move the whole selection;
- select an unselected image to return to a single-image selection; and
- double-click an image, or focus it and press Enter, to open the original in a new browser tab.

Canvas positions are saved under `~/Library/Application Support/Dohwaji/`. Dohwaji does not write to the selected folder or modify its images.

Supported formats in the first prototype are JPEG, PNG, WebP, GIF, and BMP.

## Archive by a cutoff you choose

Archiving is manual: no schedule, age rule, image labels, or daily folders. Only loose screenshots in the library root are considered; existing nested collections remain untouched.

Preview a batch from the repository directory:

```bash
python3 -m dohwaji.archive library --before 2026-10-23
```

The cutoff is **exclusive**: October 23 captures remain active. Capture dates come from `Screenshot YYYY-MM-DD at ….png` filenames (other supported image extensions also work), never modification times. Images without a recognized date are skipped and reported.

After reviewing the preview, stop Dohwaji with Control-C in its Terminal window, then apply:

```bash
python3 -m dohwaji.archive library --before 2026-10-23 --apply
```

The first batch goes into `library/archive/before-2026-10-23/`. When you choose a later cutoff, for example `2026-11-15`, the next folder is `archive/2026-10-23_to_2026-11-15/`: start inclusive, end exclusive. Each interval is one canvas. Late older screenshots go into their matching previous interval. Repeating the latest cutoff collects late arrivals and otherwise does nothing; earlier cutoffs are refused. Applying an empty batch still records the chosen boundary without creating an empty interval folder.

Filenames, image bytes, and canvas positions are preserved. Collisions, symbolic links in move paths, and unexpected pre-existing interval folders are refused. Dohwaji and the archiver lock shared state so they cannot write layouts concurrently; stop all running instances before applying. If upgrading from a version without locks, stop that server first as well. Restart Dohwaji afterward and reload the browser before using its canvases.

Move checksums, cutoff history, and original layout backups are kept outside the library under Dohwaji's application-support directory (`archives/<library-key>/history.json`). Keep this state when backing up or relocating the library; archive history and layouts are tied to its absolute path.

To preview undoing the most recent operation, or recover an interrupted operation:

```bash
python3 -m dohwaji.archive library --undo
```

Add `--apply` to perform the reversal. Undo restores original layouts and refuses to overwrite files or layouts changed since archiving. An interrupted operation must be undone before retrying the cutoff. Moving uses hard links followed by unlinking the original, so the library and its archive must be on the same filesystem with hard-link support. This is organization, not a separate backup.

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Status

The first spatial canvas prototype and a separate manual cutoff archiver are implemented. See [`docs/PLAN.md`](docs/PLAN.md) for the canvas boundary and [`docs/ARCHIVING.md`](docs/ARCHIVING.md) for the archival contract.
