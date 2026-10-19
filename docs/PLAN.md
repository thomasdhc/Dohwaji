# First Spatial Canvas Prototype

## Outcome

Create the smallest useful local prototype of Dohwaji: open one folder as a persistent, desktop-like image canvas where references can be seen together, freely arranged, and opened at full size.

The prototype should test whether the user's existing Desktop behavior can become a durable way to revisit archived references without introducing a conventional folder-navigation interface.

## Context

The user currently leaves newly captured drawing references loose on the macOS Desktop and manually groups images from the same source. Simultaneous visibility, adjacency, and spatial placement help the user compare frames and remember the material. Moving a group into a folder feels like archiving it, after which it is rarely revisited because the images are hidden behind navigation.

Dohwaji should preserve that visual and spatial behavior. The filesystem remains the storage layer; the canvas becomes the way the collection is seen.

## Decisions

- The prototype operates on one explicitly selected local folder at a time.
- Version one shows supported images that are direct children of that folder. Recursive browsing and cross-folder collections are deferred.
- The main view is a freeform two-dimensional canvas, not a folder tree or a vertically grouped gallery.
- Images retain their aspect ratios and are not cropped.
- Images can be selected individually or with a marquee, and a selected set can be dragged together; their positions persist across application restarts.
- Double-clicking an image opens the original directly in a new browser tab rather than introducing an intermediate viewer.
- Original images are read-only from Dohwaji's perspective: no moving, renaming, deleting, conversion, embedded metadata, or sidecar files in the selected folder.
- Layout state is stored separately in the user's local application data and keyed to the selected folder and image paths.
- The first implementation uses a Python standard-library local server and a vanilla browser interface so the interaction can be tested before choosing a heavier native framework.
- The canvas is scrollable and expands as images move; pan-and-zoom navigation is deferred.
- Newly discovered images begin in a deterministic five-column arrangement with a maximum displayed size of 260 by 220 pixels.
- The first prototype supports browser-native JPEG, PNG, WebP, GIF, and BMP images.
- The application does not upload images or require an external service.

## Acceptance Invariants

1. The user can launch Dohwaji for a local folder without importing or copying its images.
2. Each supported image directly under the folder appears once on the canvas, uncropped and with its aspect ratio preserved.
3. The user can marquee-select multiple images and drag the selected images together to new canvas positions.
4. After closing and reopening Dohwaji for the same folder, saved image positions are restored.
5. Double-clicking an image opens its original representation directly in a new browser tab.
6. New, removed, or renamed source files do not corrupt the saved layout; new images receive an initial position and missing images are simply absent.
7. Dohwaji does not modify the selected folder or any source image.
8. The prototype functions locally without network access.

## Plan

1. Establish a dependency-light local launcher and serve the selected folder's supported images to a minimal browser interface.
2. Render images on a scrollable freeform canvas with a deterministic initial arrangement.
3. Add single- and multi-image dragging and persist positions in local application data.
4. Open an original image directly from the canvas without an intermediate viewer.
5. Validate reopening, source-folder changes, and the no-source-modification invariant against a representative reference folder.

## Deferred

- Reading or reproducing Finder Desktop icon positions
- Infinite zoom, minimaps, and semantic zoom
- Nested folders and multiple folders on one canvas
- Source-group labels, tags, search, and filtering
- Thumbnail generation and large-library optimization
- Moving groups between active and archived areas
- Editing, deleting, renaming, or reorganizing source files
- Mobile access, synchronization, or cloud storage
