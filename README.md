# Photo Tools

Small Flask web app for managing RAW photo folders.

- **RAW Cleaner** — find RAW-only folders and convert them to DNG; delete RAW files from folders that already have DNGs.
- **Lightroom Uploader** — webhook (`POST /tools/lightroom-uploader/api/trigger`) that uploads new RAW files to an Adobe Lightroom CC catalog.
- **Album Builder** — build a "virtual album" folder of symlinks to existing photo folders (no copies).

Supported RAW formats: ORF, NEF, NRW, RAF, CR2, CR3, ARW, RW2, PEF.

## Run with Docker (recommended)

Requires an x86-64 host. DNG conversion uses Adobe DNG Converter 16.5 running under Wine.

```bash
# 1. Build the converter base image (one time, large)
docker build -t adobe-dng-converter:16.5 https://github.com/skykery/headless-adobe-dng-docker-image.git

# 2. Point it at your photos
echo "PHOTOS_DIR=/mnt/photos/DSLR" > .env

# 3. Start
docker compose up -d --build
```

Open http://<server>:5000. Settings and upload state are stored in `./data`.

Photos are mounted at the same path inside the container as on the host, so Album Builder symlinks also work outside the container. Converted DNGs are written as root.

## Run locally (macOS)

```bash
./run.sh
```

Open http://localhost:5000. Conversion uses `/Applications/Adobe DNG Converter.app` if installed, otherwise the `adobe-dng-converter:16.5` Docker image.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `BASE_DIR` | `/Volumes/Poze/DSLR` | Default photos root (can be changed in the UI) |
| `DATA_DIR` | `.` (`/data` in Docker) | Where `config.json` and `lightroom_uploaded.json` live |
| `HOST` / `PORT` | `localhost` / `5000` | Bind address |
| `FLASK_DEBUG` | `1` (`0` in Docker) | Flask debug mode |
| `DNG_CONVERTER` | unset (set in Docker) | Path to a local `convert-dng` wrapper |
| `DNG_DOCKER_IMAGE` | `adobe-dng-converter:16.5` | Converter image used by the Docker fallback |

## Tests

```bash
venv/bin/python -m pytest
```

## Security

There is no authentication. Keep it on your local network.
