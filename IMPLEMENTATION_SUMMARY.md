# Photo Tools - Implementation Summary

## Overview
A multi-tool Flask web app for photo file management. Homepage lists available tools; each tool lives under `/tools/<tool-name>`.

Currently includes:
- **RAW Cleaner** — scan, convert (RAW→DNG), and clean up folders
- **Lightroom Uploader** — webhook-driven uploader that pushes new RAW files to an Adobe Lightroom CC catalog
- **Album Builder** — group existing photo folders into a new folder of symlinks (a virtual album, no copies)

## Architecture

### Project Structure
```
app.py                          # Flask backend (all routes + logic)
templates/
  home.html                     # Homepage with tool list
  tools/
    raw_cleaner.html            # RAW Cleaner UI
    lightroom_uploader.html     # Lightroom Uploader UI
    album_builder.html          # Album Builder UI
tests/
  test_app.py                   # 94 tests
config.json                     # User config (gitignored)
lightroom_uploaded.json         # Upload state tracking (gitignored)
run.sh                          # Quick start script
requirements.txt                # flask, pytest, requests
```

### Supported RAW Formats
Shared across RAW Cleaner and Lightroom Uploader (Album Builder is format-agnostic — it works at folder level):
`.orf` (Olympus) · `.nef` `.nrw` (Nikon) · `.raf` (Fuji) · `.cr2` `.cr3` (Canon) · `.arw` (Sony) · `.rw2` (Panasonic) · `.pef` (Pentax)

### Routes

#### Homepage
- `GET /` — Tool list homepage

#### RAW Cleaner (`/tools/raw-cleaner`)
- `GET /tools/raw-cleaner` — UI
- `GET /tools/raw-cleaner/api/config` — Get base directory
- `POST /tools/raw-cleaner/api/config` — Set base directory
- `POST /tools/raw-cleaner/api/browse` — Browse directories
- `GET /tools/raw-cleaner/api/scan` — Scan for RAW+DNG folders (cleanup candidates). Returns `400 {"error": ...}` if `base_dir` is missing/unmounted
- `GET /tools/raw-cleaner/api/scan/unconverted` — Scan for RAW-only folders (conversion candidates). Same 400 behavior
- `POST /tools/raw-cleaner/api/queue` — Queue a delete job
- `POST /tools/raw-cleaner/api/convert` — Queue a conversion job
- `GET /tools/raw-cleaner/api/jobs` — Get job statuses
- `DELETE /tools/raw-cleaner/api/queue/<job_id>` — Cancel a pending job

#### Lightroom Uploader (`/tools/lightroom-uploader`)
- `GET /tools/lightroom-uploader` — UI
- `GET /tools/lightroom-uploader/api/config` — Get upload config (access_token masked)
- `POST /tools/lightroom-uploader/api/config` — Set upload_dir, Adobe client_id, access_token, catalog_id
- `POST /tools/lightroom-uploader/api/baseline` — Mark all existing RAWs in upload_dir as already-seen (no upload)
- `POST /tools/lightroom-uploader/api/trigger` — **Webhook** to call from the USB-copy script; scans upload_dir and queues upload jobs for new files
- `GET /tools/lightroom-uploader/api/status` — Returns uploaded files, baseline count, active upload jobs

#### Album Builder (`/tools/album-builder`)
- `GET /tools/album-builder` — UI
- `GET /tools/album-builder/api/config` — Get the `base_dir` the source tree anchors at (reuses RAW Cleaner's `base_dir`)
- `POST /tools/album-builder/api/browse` — List subdirectories at a given path (defaults to `base_dir`). Also returns `preview_files`: `.jpg`/`.jpeg` names directly in that folder
- `POST /tools/album-builder/api/create` — Create an album folder of symlinks. Body: `{album_dir, album_name, sources: [...]}`. Returns `{album_path, links: [{source, name}, ...]}` or 400 with `error`
- `GET /tools/album-builder/api/file?path=<jpeg>` — Stream a JPEG companion file from under `base_dir` (used by the UI for full-size previews in a new tab). Rejects paths outside `base_dir` (403), non-JPEG extensions (400), and missing files (404)

### DNG Conversion Strategy
The `convert_folder()` function uses a platform-aware approach:

1. **macOS with local app**: If `sys.platform == "darwin"` and Adobe DNG Converter is installed at `/Applications/Adobe DNG Converter.app/`, uses the native executable with flags `-c -lossy -p1 -dng1.7 -fl`
2. **Inside the app image**: If `DNG_CONVERTER` env var points to an existing file (set to `/usr/local/bin/convert-dng` by the `Dockerfile`), calls that Wine wrapper directly with flags `-c -lossy -p1 -fl -cr16.0`
3. **Fallback (Linux or macOS without app)**: Uses Docker image `adobe-dng-converter:16.5` (override with `DNG_DOCKER_IMAGE`), built locally from https://github.com/skykery/headless-adobe-dng-docker-image, with flags `-c -lossy -p1 -fl -cr16.0`. The `ensure_docker_image()` helper auto-pulls if missing.

### Running in Docker (home server)
`Dockerfile` builds `FROM adobe-dng-converter:16.5` (Debian + Wine + Adobe DNG Converter 16.5) and adds the Flask app; see the header of `docker-compose.yml` for setup. Env vars: `DATA_DIR` (config + upload state, `/data` in the image), `BASE_DIR` (default photos root), `HOST`, `PORT`, `FLASK_DEBUG`. Photos are mounted at the same path inside and outside the container so Album Builder symlinks resolve on the host.

### Lightroom Upload Strategy
`upload_to_lightroom()` calls the Adobe Lightroom Services REST API (`https://lr.adobe.io/v2`):
1. Resolve catalog id (`GET /catalog`) if not explicitly configured
2. `PUT /catalogs/{cid}/assets/{asset_id}` with metadata JSON. The `importSource` block includes `sha256` + `fileSize` so Adobe can dedupe server-side
3. On `412 Precondition Failed` from step 2 → Adobe reports the SHA-256 already matches an existing asset; skip the master upload, record locally as `duplicate:<sha256>`
4. Otherwise `PUT /catalogs/{cid}/assets/{asset_id}/master` with the binary RAW file

Authentication requires an Adobe OAuth bearer token and API client id (`Authorization: Bearer ...`, `X-API-Key: ...`).

### Album Builder Strategy
`create_album(album_dir, album_name, sources)` is fully synchronous (no worker queue) and atomic on failure:

1. Validates `album_name` (no `/`, no `\\`, not `.` / `..`, non-empty)
2. Validates `album_dir` exists and is writable
3. Refuses if the target `<album_dir>/<album_name>` already exists
4. Resolves each source's `realpath` and rejects any path not under `BASE_DIR` (same security check as `delete_raw_files()`)
5. Creates the target dir, then `os.symlink(realpath(source), <target>/<basename>)` for each source — collisions get `-2`, `-3`, … suffixes
6. If any symlink call raises, the partial album dir is removed before re-raising — never leaves a half-built album behind

Symlinks store the resolved absolute path, so the album survives being moved.

For previewing folder contents during selection, the browse endpoint also returns a `preview_files` list of `.jpg`/`.jpeg` names in the current folder. The UI renders them as a horizontally-scrollable strip of pills above the subfolder tree; clicking a pill opens the original JPEG in a new tab via `/api/file`. Folders without JPEGs show no strip. No thumbnails are generated or cached — the browser renders the original.

### Upload State & Deduplication
- State file: `lightroom_uploaded.json` (gitignored), keyed by `<folder>/<stem>` (filename without extension)
- Stem-key means that once `IMG_001.ORF` is uploaded, a later `IMG_001.DNG` in the same folder is automatically skipped — handles the RAW→DNG conversion workflow
- **Baseline snapshot** (UI button or `POST /baseline`) marks all existing RAW files as "seen but not uploaded" so pre-existing archive files are never pushed to Lightroom; only files that appear *after* the baseline are uploaded

### Background Worker
- Single daemon thread processes jobs sequentially from a shared queue
- Job types: `"convert"` → `convert_folder()`, `"delete"` → `delete_raw_files()`, `"upload"` → `upload_to_lightroom()`
- Frontends poll their respective `/api/jobs` or `/api/status` endpoints every 2 seconds
- Album Builder bypasses the queue — `create_album()` runs synchronously inside the request (sub-millisecond per symlink)

### Configuration
`config.json` (gitignored) holds settings for all tools. Album Builder reuses `base_dir` (no dedicated config key — the album destination is chosen per-creation in the UI):
```json
{
  "base_dir": "/Volumes/Poze/DSLR",
  "lightroom": {
    "upload_dir": "/Volumes/Poze/USB-Imports",
    "client_id": "adobe_api_key",
    "access_token": "oauth_bearer_token",
    "catalog_id": "optional_catalog_uuid"
  }
}
```

## Tests

98 tests across 8 test classes:

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestScanUnconvertedLogic` | 6 | `scan_unconverted()` |
| `TestScanLogic` | 9 | `scan_candidates()` |
| `TestDeleteLogic` | 5 | `delete_raw_files()` |
| `TestConvertLogic` | 8 | `convert_folder()`, `ensure_docker_image()`, macOS/Docker paths |
| `TestExpandedRawFormats` | 3 | Smoke tests for `.raf`, `.cr3`, `.arw` formats end-to-end |
| `TestLightroomUpload` | 16 | State helpers, scan dedup (including `.ORF`→`.DNG` stem case), baseline, upload success/auth/error paths, **SHA-256 in payload**, **412 duplicate handling** |
| `TestAlbumBuilder` | 7 | `create_album()` happy path, basename collision suffix, source-outside-base rejection, missing source / dest, unsafe name, existing-target rejection |
| `TestFlaskApp` | 44 | All routes (three tools), job lifecycle, config, browse (incl. `preview_files`), Lightroom trigger/baseline/status, **scan 400-when-base-missing**, **album create/browse/config/file (serve, outside-base, non-jpeg, missing)** |

Run with:
```bash
source venv/bin/activate && pytest tests/ -v
```

## Quick Start
```bash
cd cleaner
./run.sh  # or: source venv/bin/activate && python app.py
# Open http://localhost:5000
```

## Workflows

### RAW Cleaner
1. Open `http://localhost:5000` → click "RAW Cleaner"
2. Configure base path, click "Scan"
3. Convert RAW-only folders (individually or "Convert All")
4. Re-scan to see converted folders move to cleanup section
5. Approve deletion of RAW files (individually or "Approve All")

### Lightroom Uploader
1. Open `http://localhost:5000` → click "Lightroom Uploader"
2. Configure upload folder, Adobe Client ID, Access Token, (optional) Catalog ID
3. Click **"Snapshot existing as baseline"** once on first setup — marks your existing archive as already-seen so nothing old gets uploaded
4. Point your USB-copy script to the webhook URL shown at the top of the page:
   ```
   curl -X POST http://<server>:5000/tools/lightroom-uploader/api/trigger
   ```
5. Each trigger scans `upload_dir` for new RAW files (any supported camera format) and queues them for upload to your Lightroom CC catalog
6. The status section shows uploaded files, baseline count, and active jobs

### Album Builder
1. Open `http://localhost:5000` → click "Album Builder"
2. Enter the album **destination folder** (where the new album dir should be created, e.g. `/Volumes/Poze/Albums`) and a **name** for the album
3. Browse the source tree (anchored at `base_dir`) and tick the folders you want to include — each tick adds a chip to the "Selected sources" panel
4. Click **"Create album"** — the backend creates `<destination>/<name>/` and places one symlink per selected source inside, named after the source's basename (with `-2`, `-3`, … on collisions)
5. The originals are untouched. Deleting the album folder later removes only the symlinks, not the source folders.
