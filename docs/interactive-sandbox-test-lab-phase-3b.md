# Interactive Sandbox Test Lab Phase 3B

## Purpose

Phase 3B captures one bounded PNG of the exact owned AI Freelance Studio production main window after the immutable Phase 3A `1.0.0-beta.1` installation, GUI, stable-window, owned-process, and backend checks succeed. It changes no production application or Phase 3A file.

The host uses the exact Phase 3A installer, sidecar, trusted profile, network-disabled WSB settings, deadlines, process ownership policy, and cleanup behavior. Phase 3A source remains unchanged. Phase 3B uses a small tracked `production_screenshot_entry.ps1` and a separate tracked `production_screenshot_self_test.ps1` payload. The payload is produced deterministically at build time from pinned Phase 3A and screenshot sources, checked for byte-for-byte drift, and pinned by SHA-256. Runtime text injection is not used.

## Startup Boundary

The WSB LogonCommand starts only `production_screenshot_entry.ps1` through canonical Windows PowerShell. The entry script uses fixed guest paths and a fixed canonical payload command. It accepts no command, path, or arguments from the request and does not use a shell, `Invoke-Expression`, or dynamic source.

Entry and payload stages are preserved in separate atomic files rather than overwriting one marker. Entry stages are `entry_script_started`, `request_found`, `payload_found`, `payload_hash_verified`, and `payload_process_started`. The first payload statement writes `payload_interpreter_entered` before any `Add-Type`, assembly operation, C# loading, or request parsing. Initialization then records core-function loading, production API compilation, `System.Drawing` loading, screenshot API compilation, request loading, and `initialization_completed`. Initialization failures contain only bounded safe classification fields and never include a stack trace, command line, environment, path, or source.

The host allows 30 seconds from launch for the entry marker and at most 30 additional seconds for the payload marker. Neither deadline restarts the single 590-second production deadline. A known nonzero payload result is preferred over timeout: exit before `payload_interpreter_entered` returns `payload_process_exited_before_entry`, while a structured initialization failure returns `payload_initialization_failed` with its exact stage. `payload_start_timeout` is reserved for a still-running payload with no marker. Broker exit remains diagnostic and startup failures do not globally close Windows Sandbox.

`completion.json` is the normative terminal evidence. The payload writes the inherited production terminal evidence, including `completion.json`, before it exits. The static entry process then observes that exit and atomically writes optional diagnostic evidence named `entry-payload-result.json`. The only permitted reason for a successful terminal evidence set to omit this diagnostic is the bounded shared-folder race in which the host observes and validates `completion.json` before the entry process publishes the child result. The diagnostic never authorizes success and can only reject a run. Whenever it exists, the same canonical host parser immediately requires a bounded regular non-reparse file, strict JSON without duplicate keys, exact fields, schema and run identity, stage, timestamp, status, and exit code. A malformed, contradictory, wrong-run, or nonzero result fails closed even when completion already exists.

The host snapshot is written atomically only after normative terminal, screenshot, production, and cleanup validation succeeds. A diagnostic result published after that race cannot revise or replace the already validated snapshot.

## Capture Gate

Capture is blocked unless all of these are true:

- Installation passed using the pinned installer.
- The installed executable exists, is regular and non-reparse, has a SHA-256 value, and is below the verified install root.
- The retained owned process tree is nonempty and all images satisfy Phase 3A containment or auxiliary authorization.
- The exact owned window is visible, exactly titled `AI Freelance Studio`, creation-verified, GUI-eligible, and stable for at least five seconds.
- First-launch and GUI verification passed.
- The exact contained backend process is ownership-verified, image-verified, endpoint-verified, and ready.

Immediately before capture, the guest revalidates that the HWND exists; its PID is still in the owned tree; the retained creation identity and GUI eligibility still match; the exact title and visibility remain; the window is not minimized; and window/client bounds are positive and reasonable. The complete window bounds must be inside the virtual screen. The owner image must remain a contained regular image with exact basename `AI Freelance Studio.exe`. Virtual-screen dimensions are used only for containment and are never stored.

Only the verified window may be foregrounded. The guest verifies foreground ownership, waits 250 ms, then repeats HWND, owner, creation, title, visibility, minimized-state, bounds, foreground, and image checks. It repeats these checks after capture as well. No other process or window is enumerated or stored by Phase 3B.

## Capture Method

The capture thread uses per-monitor-v2 DPI awareness. Evidence records the window DPI divided by 96 as `dpi_scale`.

The guest first calls `PrintWindow` with `PW_RENDERFULLCONTENT`, then inspects the pixels. If `PrintWindow` returns false or the result is blank, transparent, black, white, uniform, low-range, or low-variance, the guest performs one controlled `CopyFromScreen` fallback. The fallback is restricted to the exact unchanged verified window bounds after foreground and final revalidation. There is no full-screen fallback.

The exact image filename is `production-main-window.png`. The image is encoded directly as PNG through `System.Drawing`; no clipboard, external screenshot tool, shell, network, or helper process is used.

## Exact Contract

The companion file is `screenshot-evidence.json`. It has this exact top-level allowlist:

```text
schema_version, protocol, run_id, profile, captured_at, source,
owned_window_verified, owner_pid, hwnd_present, exact_title_verified,
window_visible, window_bounds, client_bounds, dpi_scale, image_filename,
image_format, width, height, file_size, sha256, capture_method,
blank_check, uniformity_check, image_summary, validation_status,
errors, warnings
```

Fixed identity values are schema `1`, protocol `aifs_production_window_screenshot_v1`, profile `aifs_production_beta_1_self_test`, source `owned_window`, image filename `production-main-window.png`, and image format `png`. Successful capture methods are `print_window` and `copy_from_screen_exact_bounds`.

`window_bounds` and `client_bounds` contain exactly integer `x`, `y`, `width`, and `height`. `image_summary` contains exactly:

```text
blank, uniform, transparent, black, white, total_pixels, visible_pixels,
mean_luminance, min_luminance, max_luminance, luminance_range,
luminance_variance
```

Guest values are untrusted. The host checks exact filenames and path containment; regular non-reparse file type; one image only; no terminal temporary image; current-run capture time and file modification time; size and SHA-256; PNG signature rather than `MZ`; every chunk length and CRC; one complete image; complete bounded zlib decode; declared dimensions; and a narrow chunk allowlist. The exact structure is one `IHDR` first; at most one `sRGB`, at most one `gAMA`, and optional one structurally valid `pHYs`, all before image data; one or more contiguous `IDAT` chunks; and one zero-length `IEND` last. PNG text, time, EXIF, color-profile, unknown, and arbitrary metadata chunks are rejected. The original PNG is validated as written and is never rewritten.

The host independently reconstructs visible/nonempty pixel count, transparency, black/white/blank/uniform flags, mean/minimum/maximum/range luminance, and luminance variance. It rejects blank, transparent, black, white, uniform, low-range, and low-variance images and requires the guest summary to match exactly.

## Failure And Cleanup

Every capture attempt writes the same exact manifest schema. A failed attempt has `validation_status=failed`, a bounded safe error code, no PNG, no image size/hash/dimensions, and failed sanity checks. This preserves useful diagnostics without trusting a partial image.

Screenshot failure does not rewrite Phase 3A readiness or production status. The static payload continues into the inherited Phase 3A cleanup behavior after every capture attempt. Phase 3B host validation fails based on the screenshot contract while still independently validating the Phase 3A production evidence.

A successful Phase 3B result requires `validation_status=passed`, a valid PNG and matching host pixel summary, passed production GUI prerequisites, and complete Phase 3A cleanup. The host writes `logs/validated-screenshot-evidence.json` only after all image, timing, production, and cleanup validation succeeds. The snapshot includes the complete guest contract, independent host image summary, window/client bounds, DPI scale, capture method, and production provenance.

## External Gate

Phase 3B has an independent dual opt-in. Phase 1, fixture, and Phase 3A opt-ins do not authorize it. Plain pytest, environment opt-in alone, and broad marker expressions do not launch Sandbox. There is no automatic retry.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_SCREENSHOT_EXTERNAL = "1"
python -m pytest test_sandbox_test_lab_phase3b_external.py -m external -q -s
```

The marker expression must be exactly `external`. The pytest timeout is 600 seconds and the inherited host execution timeout is 590 seconds.

To diagnose the complete static entry and payload load boundary without another installer execution, the diagnostic payload-load probe uses the real Phase 3B entry and payload. It loads and compiles the full payload, writes the entry and payload markers, and exits before installation. Its input is a non-executable documentation file; installer, Studio, and screenshot execution are forbidden.

```powershell
$env:FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PAYLOAD_LOAD_PROBE_EXTERNAL = "1"
python -m pytest test_sandbox_test_lab_phase3b_payload_load_external.py -m external -q -s
```

## Not Implemented

Phase 3B intentionally does not implement:

- Clicks or UI automation.
- Multiple-screen capture or evidence.
- Video capture.
- Product Judge integration.
- QA, API, or production UI integration.
- Docker execution or integration.
- A Hyper-V persistent provider.
- OCR, AI visual judgment, workflow interaction, or application-content assertions.

The non-AI image checks establish only that the owned-window capture is nonempty and nondegenerate. They do not prove layout quality or application correctness.
