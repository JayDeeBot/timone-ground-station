from flask import Flask, render_template, jsonify, request, make_response, send_from_directory, Response, stream_with_context
import os
import json
from pathlib import Path
from werkzeug.utils import secure_filename
import tempfile
import shutil
import time
import queue
from collections import deque

app = Flask(__name__)

# ----------------------------
# Paths
# ----------------------------
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

# ----------------------------
# Service Worker & Manifest (root scope)
# ----------------------------
@app.route('/sw.js')
def service_worker():
    resp = make_response(send_from_directory(STATIC_DIR, 'sw.js', mimetype='application/javascript'))
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route('/manifest.json')
def manifest():
    resp = make_response(send_from_directory(STATIC_DIR, 'manifest.json', mimetype='application/json'))
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# ----------------------------
# Persistent Radio Settings
# ----------------------------
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "radio_settings.json"

DEFAULT_SETTINGS = {
    "433": {"bandwidth": 125.0, "codingRate": "4/5", "spreadingFactor": 8},
    "915": {"bandwidth": 125.0, "codingRate": "4/5", "spreadingFactor": 8},
}

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                data.setdefault(k, v)
            return data
    except Exception as e:
        print(f"Error loading settings: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(data: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving settings: {e}")
        raise

@app.route("/api/radio/settings", methods=["GET", "POST"])
def radio_settings():
    if request.method == "GET":
        return jsonify(load_settings())

    try:
        payload = request.get_json(force=True)
        freq = str(int(payload.get("frequency")))
        if freq not in ("433", "915"):
            return jsonify({"error": "frequency must be 433 or 915"}), 400

        bandwidth = float(payload.get("bandwidth"))
        coding_rate = str(payload.get("codingRate"))
        spreading_factor = int(payload.get("spreadingFactor"))

        if not (7.8 <= bandwidth <= 1625):
            return jsonify({"error": "bandwidth out of range (7.8–1625 kHz)"}), 400
        if coding_rate not in {"4/5", "4/6", "4/7", "4/8"}:
            return jsonify({"error": "invalid codingRate (must be 4/5, 4/6, 4/7, or 4/8)"}), 400
        if not (5 <= spreading_factor <= 12):
            return jsonify({"error": "spreadingFactor out of range (5–12)"}), 400

        settings = load_settings()
        settings[freq] = {
            "bandwidth": bandwidth,
            "codingRate": coding_rate,
            "spreadingFactor": spreading_factor,
        }
        save_settings(settings)
        return jsonify({"ok": True, "settings": settings})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ----------------------------
# Satellite Maps — store TL/BR only, serve full corners
# ----------------------------
MAPS_DIR = ROOT / "static" / "images" / "maps"
MAPS_DIR.mkdir(parents=True, exist_ok=True)

MAPS_INDEX = DATA_DIR / "maps_index.json"
ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

def _atomic_write_text(path: Path, text: str):
    tmp_dir = path.parent
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=tmp_dir, delete=False) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    shutil.move(tmp_name, path)

def _load_maps_index():
    if MAPS_INDEX.exists():
        try:
            return json.loads(MAPS_INDEX.read_text(encoding="utf-8"))
        except Exception as e:
            print("Failed to read maps_index.json:", e)
    return {"maps": []}

def _save_maps_index(data):
    _atomic_write_text(MAPS_INDEX, json.dumps(data, indent=2))

def _parse_lon_lat_pair(s: str):
    parts = [p.strip() for p in (s or "").split(",")]
    if len(parts) != 2:
        raise ValueError("Coordinate must be 'lon,lat'")
    lon = float(parts[0]); lat = float(parts[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError("lon/lat out of range")
    return lon, lat

def _serve_corners_from_record(rec: dict):
    """
    Backward/forward compatibility:
    - If record stores four 'corners', return them.
    - Else derive from 'tl' and 'br' (north-up image).
    """
    if "corners" in rec and isinstance(rec["corners"], dict):
        c = rec["corners"]
        try:
            return {
                "top_left":     [float(c["top_left"][0]),     float(c["top_left"][1])],
                "top_right":    [float(c["top_right"][0]),    float(c["top_right"][1])],
                "bottom_right": [float(c["bottom_right"][0]), float(c["bottom_right"][1])],
                "bottom_left":  [float(c["bottom_left"][0]),  float(c["bottom_left"][1])],
            }
        except Exception:
            pass

    # New storage format: only TL/BR are persisted
    tl = rec.get("tl")
    br = rec.get("br")
    if not (isinstance(tl, (list, tuple)) and isinstance(br, (list, tuple)) and len(tl) == 2 and len(br) == 2):
        return None
    tl_lon, tl_lat = float(tl[0]), float(tl[1])
    br_lon, br_lat = float(br[0]), float(br[1])
    tr_lon, tr_lat = br_lon, tl_lat
    bl_lon, bl_lat = tl_lon, br_lat
    return {
        "top_left":     [tl_lon, tl_lat],
        "top_right":    [tr_lon, tr_lat],
        "bottom_right": [br_lon, br_lat],
        "bottom_left":  [bl_lon, bl_lat],
    }

@app.route("/api/maps", methods=["GET", "POST"])
def api_maps():
    if request.method == "GET":
        try:
            idx = _load_maps_index()
            raw_maps = idx.get("maps", [])
            if not isinstance(raw_maps, list):
                raw_maps = []

            cleaned = []
            for m in raw_maps:
                if not isinstance(m, dict):
                    continue
                filename = m.get("filename")
                if not filename:
                    continue

                corners = _serve_corners_from_record(m)
                if not corners:
                    continue

                rec = {
                    "id": m.get("id") or Path(filename).stem,
                    "filename": filename,
                    "url": f"/static/images/maps/{filename}",
                    "corners": corners,
                }
                cleaned.append(rec)

            resp = make_response(jsonify({"maps": cleaned}))
            resp.headers["Cache-Control"] = "no-store"
            return resp
        except Exception as e:
            print("GET /api/maps failed:", e)
            return jsonify({"error": "Failed to list maps"}), 500

    # POST (upload & save)
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    filename = secure_filename(file.filename or "")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMG_EXT:
        return jsonify({"error": f"Unsupported image type: {ext}"}), 400

    # Accept either TL/BR or full four corners; normalize to TL/BR storage
    tl_str = request.form.get("top_left") or request.form.get("tl")
    br_str = request.form.get("bottom_right") or request.form.get("br")

    # Back-compat if the frontend still sends all four
    tr_str = request.form.get("top_right") or request.form.get("tr")
    bl_str = request.form.get("bottom_left") or request.form.get("bl")

    if not tl_str or not br_str:
        if all([tr_str, bl_str]):
            # derive tl/br if only tr/bl + maybe others (rare), but we need tl/br at minimum
            try:
                tr_lon, tr_lat = _parse_lon_lat_pair(tr_str)
                bl_lon, bl_lat = _parse_lon_lat_pair(bl_str)
                tl_str = f"{bl_lon},{tr_lat}"
                br_str = f"{tr_lon},{bl_lat}"
            except Exception:
                return jsonify({"error": "Provide at least top_left and bottom_right as 'lon,lat'"}), 400
        else:
            return jsonify({"error": "Provide at least top_left and bottom_right as 'lon,lat'"}), 400

    try:
        tl_lon, tl_lat = _parse_lon_lat_pair(tl_str)
        br_lon, br_lat = _parse_lon_lat_pair(br_str)
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Invalid coordinates: {e}"}), 400

    # Orientation: TL above/left of BR
    if not (tl_lat > br_lat and tl_lon < br_lon):
        return jsonify({"error": "Top-Left must be above/left of Bottom-Right"}), 400

    # Save image file (avoid overwrite with _n suffix)
    final_name = filename
    i = 1
    while (MAPS_DIR / final_name).exists():
        stem = Path(filename).stem
        final_name = f"{stem}_{i}{ext}"
        i += 1
    file.save(MAPS_DIR / final_name)

    # Index entry (store only TL/BR now)
    idx = _load_maps_index()
    map_id = (request.form.get("name") or Path(final_name).stem).strip() or Path(final_name).stem
    existing_ids = {m["id"] for m in idx.get("maps", []) if isinstance(m, dict) and "id" in m}
    orig_id = map_id
    j = 1
    while map_id in existing_ids:
        map_id = f"{orig_id}_{j}"
        j += 1

    record = {
        "id": map_id,
        "filename": final_name,
        "tl": [tl_lon, tl_lat],
        "br": [br_lon, br_lat],
    }

    idx.setdefault("maps", []).append(record)
    _save_maps_index(idx)

    # Serve full corners to clients (derived)
    corners = _serve_corners_from_record(record)
    record_out = {
        "id": map_id,
        "filename": final_name,
        "url": f"/static/images/maps/{final_name}",
        "corners": corners,
    }

    resp = make_response(jsonify({"ok": True, "map": record_out}))
    resp.headers["Cache-Control"] = "no-store"
    return resp, 201

@app.route("/api/maps/<map_id>", methods=["GET"])
def api_maps_get_one(map_id):
    try:
        idx = _load_maps_index()
        m = next((m for m in idx.get("maps", []) if isinstance(m, dict) and m.get("id") == map_id), None)
        if not m:
            return jsonify({"error": "not found"}), 404
        corners = _serve_corners_from_record(m)
        out = {
            "id": m["id"],
            "filename": m["filename"],
            "url": f"/static/images/maps/{m['filename']}",
            "corners": corners,
        }
        resp = make_response(jsonify(out))
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        print("GET /api/maps/<id> failed:", e)
        return jsonify({"error": "failed"}), 500

@app.route("/api/maps/<map_id>", methods=["DELETE"])
def api_maps_delete(map_id):
    try:
        idx = _load_maps_index()
        maps_list = idx.get("maps", [])
        if not isinstance(maps_list, list):
            maps_list = []
        rec_idx = None
        for i, m in enumerate(maps_list):
            if isinstance(m, dict) and m.get("id") == map_id:
                rec_idx = i
                break
        if rec_idx is None:
            return jsonify({"error": "not found"}), 404

        rec = maps_list.pop(rec_idx)
        try:
            fname = rec.get("filename")
            if fname:
                p = MAPS_DIR / fname
                if p.exists() and p.is_file():
                    p.unlink()
        except Exception as fe:
            print(f"File delete error for map {map_id}: {fe}")

        idx["maps"] = maps_list
        _save_maps_index(idx)
        resp = make_response(jsonify({"ok": True}))
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        print("DELETE /api/maps/<id> failed:", e)
        return jsonify({"error": "failed"}), 500


# ----------------------------
# LOG PUB/SUB (existing)
# ----------------------------
_subscribers = set()
_recent = deque(maxlen=1000)

def _subscribe():
    q = queue.Queue(maxsize=1000)
    _subscribers.add(q)
    return q

def _unsubscribe(q):
    try:
        _subscribers.remove(q)
    except KeyError:
        pass

def _publish(line: str):
    line = str(line).rstrip("\r\n")
    if not line:
        return
    _recent.append(line)
    dead = []
    for q in list(_subscribers):
        try:
            q.put_nowait(line)
        except queue.Full:
            dead.append(q)
    for q in dead:
        _unsubscribe(q)

@app.route("/api/logs/push", methods=["POST"])
def push_log():
    try:
        if request.is_json:
            payload = request.get_json(force=True, silent=True) or {}
            if "line" in payload:
                _publish(payload["line"])
            elif "lines" in payload and isinstance(payload["lines"], list):
                for ln in payload["lines"]:
                    _publish(ln)
            else:
                return jsonify({"error": "provide 'line' or 'lines' in JSON"}), 400
        else:
            body = (request.get_data(as_text=True) or "").strip("\r\n")
            if not body:
                return jsonify({"error": "empty body"}), 400
            _publish(body)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/logs/stream")
def stream_logs():
    client_q = _subscribe()
    def generate():
        try:
            for ln in list(_recent):
                yield f"data: {ln}\n\n"
            last_heartbeat = time.time()
            while True:
                try:
                    ln = client_q.get(timeout=5)
                    yield f"data: {ln}\n\n"
                except queue.Empty:
                    if time.time() - last_heartbeat > 15:
                        yield ": keep-alive\n\n"
                        last_heartbeat = time.time()
        finally:
            _unsubscribe(client_q)
    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ----------------------------
# TELEMETRY PUB/SUB — NEW
# ----------------------------
_tele_subs = set()
_tele_recent = deque(maxlen=300)  # smaller replay

def _tele_subscribe():
    q = queue.Queue(maxsize=1000)
    _tele_subs.add(q)
    return q

def _tele_unsubscribe(q):
    try:
        _tele_subs.remove(q)
    except KeyError:
        pass

def _tele_publish(d: dict):
    try:
        payload = json.dumps(d, separators=(',', ':'))
    except Exception:
        payload = json.dumps({"error":"bad_telemetry"})
    _tele_recent.append(payload)
    dead = []
    for q in list(_tele_subs):
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.append(q)
    for q in dead:
        _tele_unsubscribe(q)

@app.route("/api/telemetry/push", methods=["POST"])
def telemetry_push():
    try:
        payload = request.get_json(force=True, silent=False)
        if isinstance(payload, dict) and "rows" in payload and isinstance(payload["rows"], list):
            for row in payload["rows"]:
                if isinstance(row, dict):
                    _tele_publish(row)
        elif isinstance(payload, dict):
            _tele_publish(payload)
        else:
            return jsonify({"error": "expected JSON object or {'rows': [...]}"}), 400
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/telemetry/stream")
def telemetry_stream():
    q = _tele_subscribe()
    def generate():
        try:
            for item in list(_tele_recent):
                yield f"data: {item}\n\n"
            last_heartbeat = time.time()
            while True:
                try:
                    item = q.get(timeout=5)
                    yield f"data: {item}\n\n"
                except queue.Empty:
                    if time.time() - last_heartbeat > 15:
                        yield ": keep-alive\n\n"
                        last_heartbeat = time.time()
        finally:
            _tele_unsubscribe(q)
    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ----------------------------
# Existing Routes (unchanged)
# ----------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/files/list')
def list_files():
    base_path = os.path.expanduser('~')
    path = request.args.get('path', '/')
    requested_path = os.path.normpath(os.path.join(base_path, path.lstrip('/')))
    print(f"Requested path: {requested_path}")
    if not requested_path.startswith(base_path):
        return jsonify({'error': 'Invalid path'}), 403
    if not os.path.exists(requested_path):
        return jsonify({'error': 'Path not found'}), 404
    if not os.access(requested_path, os.R_OK):
        return jsonify({'error': 'Permission denied'}), 403
    try:
        files = []
        for item in os.listdir(requested_path):
            if item.startswith('.'):
                continue
            item_path = os.path.join(requested_path, item)
            if os.access(item_path, os.R_OK):
                files.append({
                    'name': item,
                    'path': os.path.join(path, item).replace('\\', '/'),
                    'type': 'directory' if os.path.isdir(item_path) else 'file'
                })
        return jsonify(sorted(files, key=lambda x: (x['type'] != 'directory', x['name'])))
    except Exception as e:
        print(f"Error listing files: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/view')
def view_file():
    base_path = os.path.expanduser('~')
    path = request.args.get('path', '')
    requested_path = os.path.normpath(os.path.join(base_path, path.lstrip('/')))
    if not requested_path.startswith(base_path):
        return 'Invalid file path', 403
    if not os.path.exists(requested_path) or not os.path.isfile(requested_path):
        return 'File not found', 404
    try:
        with open(requested_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return str(e), 500

if __name__ == '__main__':
    app.run(debug=True)
