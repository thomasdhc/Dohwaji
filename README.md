# Dohwaji

Dohwaji is a local-first spatial canvas for viewing and arranging drawing references.

The project is inspired by using the macOS Desktop as a visual worktable: related screenshots remain visible together, their placement carries meaning, and opening a folder should not be required to compare them.

## First prototype

The first prototype will open one local folder as a desktop-like canvas and allow the user to:

- see the folder's images together without importing or copying them;
- drag images into a useful spatial arrangement;
- reopen the folder later with that arrangement restored; and
- open an image at full size before returning to the canvas.

The original image files must remain unchanged. Layout data will be stored separately by Dohwaji.

See [`docs/PLAN.md`](docs/PLAN.md) for the prototype boundary and acceptance criteria.

## Run the prototype

Dohwaji has no third-party dependencies and requires Python 3.8 or newer. From the repository root:

```bash
python3 -m dohwaji ~/Desktop
```

The command opens Dohwaji in the default browser and keeps serving it locally until you press Control-C. Another folder can be supplied instead of `~/Desktop`; only supported images directly inside that folder are shown.

On the canvas:

- drag an image to arrange it;
- double-click an image, or focus it and press Enter, to open the full-size viewer;
- use the left and right arrow keys to move between full-size images;
- press Escape or select **Close** to return to the canvas.

Canvas positions are saved under `~/Library/Application Support/Dohwaji/`. Dohwaji does not write to the selected folder or modify its images.

Supported formats in the first prototype are JPEG, PNG, WebP, GIF, and BMP.

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Status

The first spatial canvas prototype is implemented. See [`docs/PLAN.md`](docs/PLAN.md) for its deliberately limited scope.
