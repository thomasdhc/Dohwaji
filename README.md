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

The local image collection lives under `library/`, which is intentionally excluded from Git. macOS also saves new screenshots directly into this directory. The existing `screenshots/`, `reference/`, and `new_ref/` archives remain nested beneath it.

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

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Status

The first spatial canvas prototype is implemented. See [`docs/PLAN.md`](docs/PLAN.md) for its deliberately limited scope.
