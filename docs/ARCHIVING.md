# Manual Cutoff Archiving

## Outcome and boundary

Keep the library root as the active screenshot worktable. On explicit user request, move loose screenshots before a chosen date into one folder per cutoff interval. There is no automatic schedule, retention age, per-image labeling, or content classification.

The canvas server remains read-only with respect to source images. The separate `python3 -m dohwaji.archive` command owns filesystem changes; its preview is read-only and `--apply` authorizes the displayed operation. Existing nested collections are outside discovery scope.

## Intervals

- Capture date comes from a valid `Screenshot YYYY-MM-DD at ….<supported extension>` filename, not filesystem timestamps.
- The first interval is `archive/before-<cutoff>/`.
- Subsequent intervals are `archive/<previous-cutoff>_to_<cutoff>/`, start inclusive and end exclusive.
- The caller selects every cutoff. Earlier cutoffs are refused; equal cutoffs are safe to repeat.
- Late older screenshots go to the matching recorded interval. An empty apply records the boundary without creating a folder.
- Unrecognized image filenames are reported and left in place. Non-images and nested files remain untouched.

## Integrity and state

- Preserve filenames and bytes. Refuse destination collisions, linked move paths, and existing interval folders not created by this archiver's recorded history.
- Preserve archived image coordinates and stacking order. After moving images, trim leading vertical blank space in the remaining root canvas by translating all its images upward equally until the minimum y is 64 (the normal top margin). Preserve x, relative spacing, and z; leave roots with minimum y at or below 64 unchanged. Empty roots and batches without moves require no translation. Undo restores the exact pre-archive layout, including its original vertical offset.
- Unsaved images start from the deterministic initial positions used by `static/app.js` before this root translation; changes to its grid must stay aligned with `archive.py`.
- Keep state outside the library: layouts retain their existing folder-keyed storage, and `archives/<library-key>/history.json` records cutoffs, SHA-256 hashes, source/destination paths, exact layout backups, and any pending transaction.
- Write the recovery journal before linking or removing any image. Use no-clobber same-filesystem hard links followed by source unlink, verify destination hashes, then install layouts and finalize history. POSIX directory metadata is synced as well as journal contents; the standard library does not provide directory fsync on Windows.
- A process interruption may leave both links or a partially moved batch; preview and apply `--undo` to restore it before retrying. Undo is itself journaled/recoverable and refuses modified images or layouts. This command does not promise recovery from arbitrary external modification or storage failure.
- Shared server locks and exclusive archive locks use the same state-root lock file. Servers retain their locks until request threads finish. Stop older, lock-unaware server versions explicitly before using the archiver.

## Verification

Automated tests cover cutoff boundaries, dry-run immutability, consecutive and repeated cutoffs, late captures, original bytes/layouts, unknown filenames, collisions, symlinks, unmanaged folders, runtime locking, undo safeguards, and interruption recovery. Library files and runtime state remain untracked.
