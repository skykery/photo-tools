import os
import sys
import json
import hashlib
import threading
import time
import uuid
import subprocess
from collections import deque
import requests
from flask import Flask, render_template, jsonify, request, send_file

app = Flask(__name__)

# Configuration management
# DATA_DIR holds config + upload state; mount it as a volume when running in Docker.
DATA_DIR = os.environ.get("DATA_DIR", ".")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DEFAULT_BASE_DIR = os.environ.get("BASE_DIR", "/Volumes/Poze/DSLR")
RAW_EXTENSIONS = {
    ".orf",   # Olympus
    ".nef",   # Nikon
    ".nrw",   # Nikon (compact)
    ".raf",   # Fuji
    ".cr2",   # Canon (older)
    ".cr3",   # Canon (newer)
    ".arw",   # Sony
    ".rw2",   # Panasonic
    ".pef",   # Pentax
}
DNG_EXTENSION = ".dng"
DOCKER_IMAGE = os.environ.get("DNG_DOCKER_IMAGE", "adobe-dng-converter:16.5")
MACOS_DNG_EXECUTABLE = "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter"
# Wine wrapper baked into the app image (convert-dng from headless-adobe-dng-docker-image).
# When set, conversion runs it directly instead of starting a Docker container.
LOCAL_DNG_CONVERTER = os.environ.get("DNG_CONVERTER", "")

LIGHTROOM_STATE_FILE = os.path.join(DATA_DIR, "lightroom_uploaded.json")
LIGHTROOM_API_BASE = "https://lr.adobe.io/v2"


def load_full_config():
    """Load full config dict from config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_full_config(config):
    """Save the full config dict to config.json."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def load_config():
    """Load base_dir from config.json, fall back to default if missing."""
    return load_full_config().get("base_dir", DEFAULT_BASE_DIR)


def save_config(path):
    """Save base_dir to config.json, preserving other config keys."""
    config = load_full_config()
    config["base_dir"] = path
    save_full_config(config)


BASE_DIR = load_config()
LIGHTROOM_CONFIG = load_full_config().get("lightroom", {})


def save_lightroom_config(lr_config):
    """Merge lightroom settings into config.json, preserving other keys."""
    config = load_full_config()
    existing = config.get("lightroom", {})
    existing.update(lr_config)
    config["lightroom"] = existing
    save_full_config(config)
    return existing


def _state_key(file_path):
    """Return '<dir>/<stem>' — extension-stripped key for dedup across formats."""
    folder = os.path.dirname(file_path)
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return os.path.join(folder, stem)


def _sha256_of_file(file_path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def load_upload_state():
    """Returns {state_key: {asset_id, uploaded_at, original_path, baseline?}}."""
    if os.path.exists(LIGHTROOM_STATE_FILE):
        try:
            with open(LIGHTROOM_STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_upload_state(state):
    """Persist state atomically via temp file + rename."""
    tmp = LIGHTROOM_STATE_FILE + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, LIGHTROOM_STATE_FILE)


def mark_uploaded(file_path, asset_id):
    """Record a successful upload in state."""
    state = load_upload_state()
    state[_state_key(file_path)] = {
        "asset_id": asset_id,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "original_path": file_path,
    }
    save_upload_state(state)


def mark_baseline(file_path):
    """Record a file as 'seen but not uploaded' so it won't be queued."""
    state = load_upload_state()
    key = _state_key(file_path)
    if key not in state:
        state[key] = {
            "asset_id": None,
            "uploaded_at": None,
            "original_path": file_path,
            "baseline": True,
        }
        save_upload_state(state)
        return True
    return False

# Job queue state
job_queue = deque()
job_states = {}
queue_lock = threading.Lock()


def scan_candidates(base_path=BASE_DIR):
    """
    Recursively scan base_path for directories containing both RAW and DNG files.
    Returns list of candidate folders.
    """
    candidates = []

    if not os.path.isdir(base_path):
        return candidates

    for root, dirs, files in os.walk(base_path):
        # Collect RAW and DNG files, excluding macOS metadata files
        raw_files = []
        dng_count = 0

        for filename in files:
            # Skip macOS metadata files (e.g., ._filename)
            if filename.startswith("._"):
                continue

            lower_name = filename.lower()
            if any(lower_name.endswith(ext) for ext in RAW_EXTENSIONS):
                raw_files.append(filename)
            elif lower_name.endswith(DNG_EXTENSION):
                dng_count += 1

        # A folder is a candidate if it has both RAW and DNG files
        if raw_files and dng_count > 0:
            candidates.append({
                "path": root,
                "raw_files": raw_files,
                "dng_count": dng_count,
                "raw_count": len(raw_files)
            })

    return candidates


def scan_unconverted(base_path=BASE_DIR):
    """
    Recursively scan base_path for directories with RAW files but no DNG files.
    Returns list of unconverted candidate folders.
    """
    candidates = []

    if not os.path.isdir(base_path):
        return candidates

    for root, dirs, files in os.walk(base_path):
        # Collect RAW and DNG files, excluding macOS metadata files
        raw_files = []
        dng_count = 0

        for filename in files:
            # Skip macOS metadata files (e.g., ._filename)
            if filename.startswith("._"):
                continue

            lower_name = filename.lower()
            if any(lower_name.endswith(ext) for ext in RAW_EXTENSIONS):
                raw_files.append(filename)
            elif lower_name.endswith(DNG_EXTENSION):
                dng_count += 1

        # A folder is a candidate if it has RAW files but zero DNG files
        if raw_files and dng_count == 0:
            candidates.append({
                "path": root,
                "raw_files": raw_files,
                "raw_count": len(raw_files)
            })

    return candidates


def delete_raw_files(folder_path, base_dir=None):
    """
    Delete RAW files (.orf, .nef) from folder_path.
    Returns dict with deleted files and errors.
    """
    if base_dir is None:
        base_dir = BASE_DIR

    # Security check: path must start with base_dir
    real_path = os.path.realpath(folder_path)
    real_base = os.path.realpath(base_dir)

    if not real_path.startswith(real_base):
        raise ValueError(f"Path '{folder_path}' is outside base directory")

    deleted = []
    errors = []

    if not os.path.isdir(folder_path):
        errors.append(f"Directory not found: {folder_path}")
        return deleted, errors

    try:
        for filename in os.listdir(folder_path):
            lower_name = filename.lower()
            if any(lower_name.endswith(ext) for ext in RAW_EXTENSIONS):
                filepath = os.path.join(folder_path, filename)
                try:
                    os.remove(filepath)
                    deleted.append(filename)
                except Exception as e:
                    errors.append(f"Failed to delete {filename}: {str(e)}")
    except Exception as e:
        errors.append(f"Error reading directory: {str(e)}")

    return deleted, errors


def ensure_docker_image():
    """Check if the Docker image exists locally; pull from registry if not."""
    result = subprocess.run(
        ["docker", "image", "inspect", DOCKER_IMAGE],
        capture_output=True
    )
    if result.returncode != 0:
        subprocess.run(
            ["docker", "pull", DOCKER_IMAGE],
            check=True
        )


def convert_folder(folder_path, base_dir=None):
    """
    Convert RAW files (.orf, .nef) in folder_path to DNG using Docker-based Adobe DNG Converter.
    Returns tuple: (converted_count, errors_list)
    """
    if base_dir is None:
        base_dir = BASE_DIR

    # Security check: path must start with base_dir
    real_path = os.path.realpath(folder_path)
    real_base = os.path.realpath(base_dir)

    if not real_path.startswith(real_base):
        raise ValueError(f"Path '{folder_path}' is outside base directory")

    errors = []
    converted_count = 0

    if not os.path.isdir(folder_path):
        errors.append(f"Directory not found: {folder_path}")
        return converted_count, errors

    # Enumerate RAW files (case-insensitive, skip ._* files)
    raw_files = []
    try:
        for filename in os.listdir(folder_path):
            if filename.startswith("._"):
                continue
            lower_name = filename.lower()
            if any(lower_name.endswith(ext) for ext in RAW_EXTENSIONS):
                raw_files.append(filename)
    except Exception as e:
        errors.append(f"Error reading directory: {str(e)}")
        return converted_count, errors

    if not raw_files:
        return converted_count, errors

    if sys.platform == "darwin" and os.path.isfile(MACOS_DNG_EXECUTABLE):
        # macOS with local app installed
        cmd = [MACOS_DNG_EXECUTABLE, "-c", "-lossy", "-p1", "-dng1.7", "-fl"]
        for filename in raw_files:
            cmd.append(os.path.join(folder_path, filename))
    elif LOCAL_DNG_CONVERTER and os.path.isfile(LOCAL_DNG_CONVERTER):
        # Running inside the combined app image: call the Wine wrapper directly
        cmd = [LOCAL_DNG_CONVERTER, "-c", "-lossy", "-p1", "-fl", "-cr16.0"]
        for filename in raw_files:
            cmd.append(os.path.join(folder_path, filename))
    else:
        # Linux or macOS without local app: use Docker
        ensure_docker_image()
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{folder_path}:/work",
            DOCKER_IMAGE,
            "-c", "-lossy", "-p1", "-fl", "-cr16.0"
        ]
        for filename in raw_files:
            cmd.append(f"/work/{filename}")

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        converted_count = len(raw_files)
    except subprocess.CalledProcessError as e:
        errors.append(f"Conversion failed with code {e.returncode}: {e.stderr}")
    except Exception as e:
        errors.append(f"Error running converter: {str(e)}")

    return converted_count, errors


def scan_for_upload(upload_dir):
    """
    Walk upload_dir recursively, return abs paths of RAW files (RAW_EXTENSIONS)
    whose _state_key is NOT already in load_upload_state(). Skip ._* files.
    """
    if not os.path.isdir(upload_dir):
        return []

    state = load_upload_state()
    new_files = []
    for root, dirs, files in os.walk(upload_dir):
        for filename in files:
            if filename.startswith("._"):
                continue
            lower = filename.lower()
            if not any(lower.endswith(ext) for ext in RAW_EXTENSIONS):
                continue
            full_path = os.path.join(root, filename)
            if _state_key(full_path) not in state:
                new_files.append(full_path)
    return new_files


def baseline_upload_dir(upload_dir):
    """Mark every existing RAW file in upload_dir as 'seen' without uploading."""
    if not os.path.isdir(upload_dir):
        return 0

    count = 0
    for root, dirs, files in os.walk(upload_dir):
        for filename in files:
            if filename.startswith("._"):
                continue
            lower = filename.lower()
            if not any(lower.endswith(ext) for ext in RAW_EXTENSIONS):
                continue
            full_path = os.path.join(root, filename)
            if mark_baseline(full_path):
                count += 1
    return count


def upload_to_lightroom(file_path, client_id, access_token, catalog_id=None):
    """
    Upload a single RAW file to Adobe Lightroom CC.
    Returns (asset_id, errors).
    """
    errors = []

    if not os.path.isfile(file_path):
        return None, [f"File not found: {file_path}"]
    if not client_id or not access_token:
        return None, ["Missing Adobe client_id or access_token"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-API-Key": client_id,
    }

    try:
        if not catalog_id:
            r = requests.get(f"{LIGHTROOM_API_BASE}/catalog", headers=headers, timeout=30)
            if r.status_code != 200:
                return None, [f"Failed to resolve catalog: {r.status_code} {r.text[:200]}"]
            # Adobe responses are prefixed with `while (1) {}` — strip it
            body = r.text.lstrip()
            if body.startswith("while"):
                body = body.split("\n", 1)[1] if "\n" in body else body
            catalog_id = json.loads(body).get("id")
            if not catalog_id:
                return None, ["Could not parse catalog id from Adobe response"]

        asset_id = uuid.uuid4().hex
        filename = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        sha256 = _sha256_of_file(file_path)

        metadata = {
            "subtype": "image",
            "payload": {
                "captureDate": "0000-00-00T00:00:00",
                "importSource": {
                    "fileName": filename,
                    "fileSize": size,
                    "sha256": sha256,
                    "importedOnDevice": "raw-cleaner",
                    "importedBy": client_id,
                    "importTimestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            },
        }
        meta_url = f"{LIGHTROOM_API_BASE}/catalogs/{catalog_id}/assets/{asset_id}"
        r = requests.put(
            meta_url,
            headers={**headers, "Content-Type": "application/json"},
            data=json.dumps(metadata),
            timeout=30,
        )
        if r.status_code == 412:
            # Adobe-side dedup: sha256 matched an existing asset, skip the master upload.
            return f"duplicate:{sha256}", []
        if r.status_code not in (200, 201, 204):
            return None, [f"Create asset failed: {r.status_code} {r.text[:200]}"]

        master_url = f"{LIGHTROOM_API_BASE}/catalogs/{catalog_id}/assets/{asset_id}/master"
        with open(file_path, 'rb') as f:
            r = requests.put(
                master_url,
                headers={**headers, "Content-Type": "application/octet-stream"},
                data=f,
                timeout=300,
            )
        if r.status_code not in (200, 201, 204):
            return None, [f"Upload master failed: {r.status_code} {r.text[:200]}"]

        return asset_id, errors
    except requests.RequestException as e:
        return None, [f"Network error: {str(e)}"]
    except Exception as e:
        return None, [f"Upload error: {str(e)}"]


def worker_thread():
    """
    Background worker that processes jobs from the queue sequentially.
    """
    while True:
        job_id = None

        with queue_lock:
            if job_queue:
                job_id = job_queue.popleft()

        if job_id:
            with queue_lock:
                job_states[job_id]["status"] = "running"

            try:
                folder_path = job_states[job_id]["path"]
                job_type = job_states[job_id].get("type", "delete")

                if job_type == "convert":
                    converted, errors = convert_folder(folder_path)
                    with queue_lock:
                        job_states[job_id]["status"] = "done"
                        job_states[job_id]["converted"] = converted
                        job_states[job_id]["errors"] = errors
                elif job_type == "upload":
                    cfg = LIGHTROOM_CONFIG
                    asset_id, errors = upload_to_lightroom(
                        folder_path,
                        cfg.get("client_id", ""),
                        cfg.get("access_token", ""),
                        cfg.get("catalog_id"),
                    )
                    if asset_id:
                        mark_uploaded(folder_path, asset_id)
                    with queue_lock:
                        job_states[job_id]["status"] = "done" if asset_id else "error"
                        job_states[job_id]["asset_id"] = asset_id
                        job_states[job_id]["errors"] = errors
                else:  # "delete"
                    deleted, errors = delete_raw_files(folder_path)
                    with queue_lock:
                        job_states[job_id]["status"] = "done"
                        job_states[job_id]["deleted"] = deleted
                        job_states[job_id]["errors"] = errors
            except Exception as e:
                with queue_lock:
                    job_states[job_id]["status"] = "error"
                    job_states[job_id]["errors"] = [str(e)]

        time.sleep(0.5)


# Start the worker thread as a daemon
worker = threading.Thread(target=worker_thread, daemon=True)
worker.start()


@app.route("/")
def home():
    """Serve the homepage with tool list."""
    return render_template("home.html")


@app.route("/tools/raw-cleaner")
def raw_cleaner():
    """Serve the RAW Cleaner tool UI."""
    return render_template("tools/raw_cleaner.html")


@app.route("/tools/raw-cleaner/api/config", methods=["GET"])
def get_config():
    """Get current configuration."""
    return jsonify({"base_dir": BASE_DIR}), 200


@app.route("/tools/raw-cleaner/api/browse", methods=["POST"])
def browse_directories():
    """List subdirectories at a given path."""
    data = request.get_json()
    path = data.get("path", "/Volumes")

    if not path:
        path = "/Volumes"

    # Normalize path
    path = os.path.expanduser(path)

    if not os.path.isdir(path):
        return jsonify({"error": "Path does not exist or is not a directory"}), 400

    try:
        entries = []
        for item in sorted(os.listdir(path)):
            # Skip hidden files and system files
            if item.startswith("."):
                continue

            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                try:
                    # Check if readable
                    os.listdir(item_path)
                    entries.append({
                        "name": item,
                        "path": item_path,
                        "type": "directory"
                    })
                except PermissionError:
                    # Skip directories we can't read
                    pass

        return jsonify({
            "current_path": path,
            "parent_path": os.path.dirname(path),
            "entries": entries
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/tools/raw-cleaner/api/config", methods=["POST"])
def set_config():
    """Set base directory configuration."""
    global BASE_DIR
    data = request.get_json()
    path = data.get("base_dir")

    if not path:
        return jsonify({"error": "base_dir required"}), 400

    # Validate that path exists and is a directory
    if not os.path.isdir(path):
        return jsonify({"error": "Path does not exist or is not a directory"}), 400

    # Save to config file
    save_config(path)
    BASE_DIR = path

    return jsonify({"base_dir": BASE_DIR}), 200


@app.route("/tools/raw-cleaner/api/scan", methods=["GET"])
def scan():
    """Scan for candidate folders with RAW+DNG and return JSON."""
    if not os.path.isdir(BASE_DIR):
        return jsonify({
            "error": f"Base directory is not accessible: {BASE_DIR}",
            "base_dir": BASE_DIR,
        }), 400
    candidates = scan_candidates(BASE_DIR)
    return jsonify(candidates)


@app.route("/tools/raw-cleaner/api/scan/unconverted", methods=["GET"])
def scan_unconverted_route():
    """Scan for candidate folders with RAW only (no DNG) and return JSON."""
    if not os.path.isdir(BASE_DIR):
        return jsonify({
            "error": f"Base directory is not accessible: {BASE_DIR}",
            "base_dir": BASE_DIR,
        }), 400
    candidates = scan_unconverted(BASE_DIR)
    return jsonify(candidates)


@app.route("/tools/raw-cleaner/api/queue", methods=["POST"])
def queue_job():
    """Add a folder to the deletion queue."""
    data = request.get_json()
    path = data.get("path")
    job_type = data.get("type", "delete")

    if not path:
        return jsonify({"error": "Path required"}), 400

    # Security check
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(BASE_DIR)

    if not real_path.startswith(real_base):
        return jsonify({"error": "Path outside base directory"}), 403

    job_id = str(uuid.uuid4())

    with queue_lock:
        job_states[job_id] = {
            "id": job_id,
            "path": path,
            "type": job_type,
            "status": "pending",
            "deleted": [],
            "converted": 0,
            "errors": []
        }
        job_queue.append(job_id)

    return jsonify({"job_id": job_id, "status": "pending"}), 200


@app.route("/tools/raw-cleaner/api/convert", methods=["POST"])
def queue_convert():
    """Add a folder to the conversion queue."""
    data = request.get_json()
    path = data.get("path")

    if not path:
        return jsonify({"error": "Path required"}), 400

    # Security check
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(BASE_DIR)

    if not real_path.startswith(real_base):
        return jsonify({"error": "Path outside base directory"}), 403

    job_id = str(uuid.uuid4())

    with queue_lock:
        job_states[job_id] = {
            "id": job_id,
            "path": path,
            "type": "convert",
            "status": "pending",
            "converted": 0,
            "errors": []
        }
        job_queue.append(job_id)

    return jsonify({"job_id": job_id, "status": "pending"}), 200


@app.route("/tools/raw-cleaner/api/jobs", methods=["GET"])
def get_jobs():
    """Return the current job queue status."""
    with queue_lock:
        jobs = list(job_states.values())

    return jsonify(jobs)


@app.route("/tools/raw-cleaner/api/queue/<job_id>", methods=["DELETE"])
def cancel_job(job_id):
    """Remove a pending job from the queue."""
    with queue_lock:
        if job_id in job_states:
            job = job_states[job_id]
            if job["status"] == "pending":
                # Remove from queue
                job_queue_list = list(job_queue)
                if job_id in job_queue_list:
                    job_queue_list.remove(job_id)
                job_queue.clear()
                job_queue.extend(job_queue_list)
                del job_states[job_id]
                return jsonify({"status": "cancelled"}), 200
            else:
                return jsonify({"error": "Job is not pending"}), 400
        else:
            return jsonify({"error": "Job not found"}), 404


# ---------------------------------------------------------------------------
# Lightroom Uploader routes
# ---------------------------------------------------------------------------


@app.route("/tools/lightroom-uploader")
def lightroom_uploader():
    """Serve the Lightroom Uploader tool UI."""
    return render_template("tools/lightroom_uploader.html")


def _mask_token(token):
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


@app.route("/tools/lightroom-uploader/api/config", methods=["GET"])
def lr_get_config():
    """Return Lightroom config (access_token masked)."""
    cfg = LIGHTROOM_CONFIG
    return jsonify({
        "upload_dir": cfg.get("upload_dir", ""),
        "client_id": cfg.get("client_id", ""),
        "access_token_masked": _mask_token(cfg.get("access_token", "")),
        "catalog_id": cfg.get("catalog_id", ""),
        "has_access_token": bool(cfg.get("access_token")),
    }), 200


@app.route("/tools/lightroom-uploader/api/config", methods=["POST"])
def lr_set_config():
    """Update Lightroom config — merges with existing values."""
    global LIGHTROOM_CONFIG
    data = request.get_json() or {}

    upload_dir = data.get("upload_dir")
    if upload_dir and not os.path.isdir(upload_dir):
        return jsonify({"error": "upload_dir does not exist or is not a directory"}), 400

    update = {}
    for key in ("upload_dir", "client_id", "access_token", "catalog_id"):
        if key in data and data[key] != "":
            update[key] = data[key]

    LIGHTROOM_CONFIG = save_lightroom_config(update)
    return jsonify({
        "upload_dir": LIGHTROOM_CONFIG.get("upload_dir", ""),
        "client_id": LIGHTROOM_CONFIG.get("client_id", ""),
        "access_token_masked": _mask_token(LIGHTROOM_CONFIG.get("access_token", "")),
        "catalog_id": LIGHTROOM_CONFIG.get("catalog_id", ""),
        "has_access_token": bool(LIGHTROOM_CONFIG.get("access_token")),
    }), 200


@app.route("/tools/lightroom-uploader/api/baseline", methods=["POST"])
def lr_baseline():
    """Mark all existing RAW files in upload_dir as 'seen', without uploading."""
    upload_dir = LIGHTROOM_CONFIG.get("upload_dir")
    if not upload_dir:
        return jsonify({"error": "upload_dir not configured"}), 400
    if not os.path.isdir(upload_dir):
        return jsonify({"error": "upload_dir does not exist"}), 400

    marked = baseline_upload_dir(upload_dir)
    return jsonify({"marked": marked}), 200


@app.route("/tools/lightroom-uploader/api/trigger", methods=["POST"])
def lr_trigger():
    """
    Webhook: scan upload_dir for new RAW files and enqueue upload jobs.
    Optional body: {"path": "/override/upload_dir"}
    """
    data = request.get_json(silent=True) or {}
    upload_dir = data.get("path") or LIGHTROOM_CONFIG.get("upload_dir")

    if not upload_dir:
        return jsonify({"error": "upload_dir not configured"}), 400
    if not os.path.isdir(upload_dir):
        return jsonify({"error": f"upload_dir not found: {upload_dir}"}), 400

    new_files = scan_for_upload(upload_dir)
    job_ids = []
    with queue_lock:
        for file_path in new_files:
            job_id = str(uuid.uuid4())
            job_states[job_id] = {
                "id": job_id,
                "path": file_path,
                "type": "upload",
                "status": "pending",
                "asset_id": None,
                "errors": [],
            }
            job_queue.append(job_id)
            job_ids.append(job_id)

    return jsonify({
        "queued": len(job_ids),
        "job_ids": job_ids,
        "scanned_dir": upload_dir,
    }), 200


@app.route("/tools/lightroom-uploader/api/status", methods=["GET"])
def lr_status():
    """Return uploaded files and pending upload jobs."""
    state = load_upload_state()
    uploaded = [
        {"key": key, **value}
        for key, value in state.items()
        if value.get("asset_id")
    ]
    baseline_count = sum(1 for v in state.values() if v.get("baseline"))

    with queue_lock:
        upload_jobs = [j for j in job_states.values() if j.get("type") == "upload"]

    return jsonify({
        "uploaded": uploaded,
        "baseline_count": baseline_count,
        "upload_jobs": upload_jobs,
    }), 200


# ---------------------------------------------------------------------------
# Album Builder routes
# ---------------------------------------------------------------------------


def create_album(album_dir, album_name, sources, base_dir=None):
    """
    Create <album_dir>/<album_name>/ with symlinks to each source folder.
    Returns (album_path, [{"source": ..., "name": ...}, ...]).
    Raises ValueError on any validation failure; no partial album is created.
    """
    if base_dir is None:
        base_dir = BASE_DIR

    if not isinstance(album_name, str) or album_name == "":
        raise ValueError("album_name is required")
    if "/" in album_name or "\\" in album_name or album_name in (".", ".."):
        raise ValueError(f"album_name contains illegal characters: {album_name!r}")

    if not album_dir or not os.path.isdir(album_dir):
        raise ValueError(f"album_dir is not an accessible directory: {album_dir}")
    if not os.access(album_dir, os.W_OK):
        raise ValueError(f"album_dir is not writable: {album_dir}")

    target = os.path.join(album_dir, album_name)
    if os.path.exists(target) or os.path.islink(target):
        raise ValueError(f"Album already exists: {target}")

    if not sources:
        raise ValueError("At least one source folder is required")

    real_base = os.path.realpath(base_dir)
    resolved_sources = []
    for src in sources:
        if not src:
            raise ValueError("Source path cannot be empty")
        real_src = os.path.realpath(src)
        if not (real_src == real_base or real_src.startswith(real_base + os.sep)):
            raise ValueError(f"Source is outside base directory: {src}")
        if not os.path.isdir(real_src):
            raise ValueError(f"Source is not a directory: {src}")
        resolved_sources.append(real_src)

    os.mkdir(target)
    links = []
    used_names = set()
    try:
        for real_src in resolved_sources:
            base_name = os.path.basename(real_src.rstrip(os.sep)) or "source"
            name = base_name
            suffix = 2
            while name in used_names or os.path.lexists(os.path.join(target, name)):
                name = f"{base_name}-{suffix}"
                suffix += 1
            os.symlink(real_src, os.path.join(target, name))
            used_names.add(name)
            links.append({"source": real_src, "name": name})
    except Exception:
        # Best-effort rollback: remove anything we managed to create.
        for link in links:
            try:
                os.unlink(os.path.join(target, link["name"]))
            except OSError:
                pass
        try:
            os.rmdir(target)
        except OSError:
            pass
        raise

    return target, links


@app.route("/tools/album-builder")
def album_builder():
    """Serve the Album Builder tool UI."""
    return render_template("tools/album_builder.html")


@app.route("/tools/album-builder/api/config", methods=["GET"])
def album_get_config():
    """Return the base_dir the album source tree should anchor at."""
    return jsonify({"base_dir": BASE_DIR}), 200


@app.route("/tools/album-builder/api/browse", methods=["POST"])
def album_browse():
    """List subdirectories at a given path (defaults to BASE_DIR)."""
    data = request.get_json(silent=True) or {}
    path = data.get("path") or BASE_DIR
    path = os.path.expanduser(path)

    if not os.path.isdir(path):
        return jsonify({
            "error": f"Path is not accessible: {path}",
            "path": path,
        }), 400

    try:
        entries = []
        preview_files = []
        for item in sorted(os.listdir(path)):
            if item.startswith("."):
                continue
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                try:
                    os.listdir(item_path)
                    entries.append({
                        "name": item,
                        "path": item_path,
                        "type": "directory",
                    })
                except PermissionError:
                    pass
            elif os.path.isfile(item_path):
                lower = item.lower()
                if lower.endswith(".jpg") or lower.endswith(".jpeg"):
                    preview_files.append({"name": item, "path": item_path})

        return jsonify({
            "current_path": path,
            "parent_path": os.path.dirname(path),
            "base_dir": BASE_DIR,
            "entries": entries,
            "preview_files": preview_files,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/tools/album-builder/api/file", methods=["GET"])
def album_file():
    """Stream a JPEG companion file from under BASE_DIR for previewing in a new tab."""
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path required"}), 400

    real = os.path.realpath(path)
    real_base = os.path.realpath(BASE_DIR)
    if not (real == real_base or real.startswith(real_base + os.sep)):
        return jsonify({"error": "Path outside base directory"}), 403

    lower = real.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg")):
        return jsonify({"error": "Only JPEG files are servable"}), 400

    if not os.path.isfile(real):
        return jsonify({"error": "File not found"}), 404

    return send_file(real, mimetype="image/jpeg")


@app.route("/tools/album-builder/api/create", methods=["POST"])
def album_create():
    """Create an album folder containing symlinks to the selected sources."""
    data = request.get_json(silent=True) or {}
    album_dir = data.get("album_dir", "")
    album_name = data.get("album_name", "")
    sources = data.get("sources") or []

    try:
        album_path, links = create_album(album_dir, album_name, sources)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"Filesystem error: {e}"}), 400

    return jsonify({
        "album_path": album_path,
        "links": links,
    }), 200


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
        host=os.environ.get("HOST", "localhost"),
        port=int(os.environ.get("PORT", "5000")),
    )
