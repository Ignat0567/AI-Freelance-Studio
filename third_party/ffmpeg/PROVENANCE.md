# Bundled ffmpeg — provenance

This directory is where the app's video-evidence feature (Sandbox Test Lab Phase 5d,
`sandbox_test_lab/video_evidence.py`) expects to find a trusted `ffmpeg.exe`. The binary
itself is **not committed** (see `.gitignore`) — it must be downloaded and placed here
manually, then hash-pinned in `video_evidence.py`'s `TRUSTED_FFMPEG_SHA256`.

## Why this specific build

- **Codec: VP9 in a WebM container.** Royalty-free by design (`libvpx` is BSD-licensed,
  VP9 carries no patent-licensing exposure), chosen specifically to avoid H.264's patent
  situation for this commercial closed-source product.
- **License: LGPL-shared ffmpeg build only — never a GPL build.** A GPL build (one that
  compiles in `libx264` or other GPL-only components) would carry real GPL compliance
  obligations if redistributed inside closed-source software. Only use a build explicitly
  labeled LGPL/shared.

## How to obtain it

1. Go to <https://github.com/BtbN/FFmpeg-Builds/releases> (this project explicitly labels
   GPL vs LGPL vs static vs shared in each asset's filename, unlike most other prebuilt
   ffmpeg distributions).
2. Download the asset named like `ffmpeg-master-latest-win64-lgpl-shared.zip` (or the
   date-stamped equivalent — the exact version doesn't matter, only that it says
   **win64**, **lgpl**, and **shared**).
3. Extract `bin/ffmpeg.exe` from the zip to `third_party/ffmpeg/ffmpeg.exe` in this repo.
4. Also copy the build's `LICENSE`/`COPYING.LGPLv2.1` and any third-party notices file from
   the zip into this directory, so the LGPL attribution ships alongside the binary.
5. Record below: the exact release tag/date you downloaded, and the download URL.
6. Ask the assistant (or run `sha256_file()` from `sandbox_test_lab/workspace.py`) to
   compute the SHA-256 of the placed `ffmpeg.exe`, and fill that into
   `TRUSTED_FFMPEG_SHA256` in `sandbox_test_lab/video_evidence.py`.

## Provenance record (fill in once downloaded)

- Release tag / date downloaded: _(not yet vendored)_
- Download URL: _(not yet vendored)_
- SHA-256 of `ffmpeg.exe`: _(not yet vendored — see `TRUSTED_FFMPEG_SHA256`)_
