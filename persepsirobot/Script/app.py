import os
import json
import queue
import threading
import subprocess
import time
from flask import Flask, render_template, request, jsonify, Response, send_from_directory

app = Flask(__name__)

# =========================================================
# STATE
# =========================================================

capture_proc = None
capture_lock = threading.Lock()
capture_log_queue = queue.Queue()

odm_proc = None
odm_lock = threading.Lock()
odm_log_queue = queue.Queue()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================================================
# CONFIG DEFAULTS
# =========================================================

config = {
    "esp32_ip": "http://192.168.200.219",
    "camera_device": "/dev/v4l/by-id/usb-FEC_NYK_NEMESIS_202001010001-video-index0",
    "save_dir": os.path.join(os.path.dirname(SCRIPT_DIR), "datasets"),
    "frame_width": 1280,
    "frame_height": 720,
    "relay_id": 0,
    "node_host": "localhost",
    "node_port": 3000,
    "odm_input": os.path.join(os.path.dirname(SCRIPT_DIR), "datasets"),
    "odm_output": os.path.join(os.path.dirname(SCRIPT_DIR), "output"),
}

# =========================================================
# HELPERS
# =========================================================

def stream_output(proc, log_queue):
    """Read stdout+stderr from proc and push lines to queue."""
    for line in iter(proc.stdout.readline, b""):
        log_queue.put(line.decode("utf-8", errors="replace").rstrip())
    log_queue.put(None)  # sentinel


def sse_stream(log_queue):
    """Generator that yields SSE events from queue."""
    while True:
        try:
            line = log_queue.get(timeout=30)
        except queue.Empty:
            yield "event: ping\ndata: \n\n"
            continue
        if line is None:
            yield "event: done\ndata: process finished\n\n"
            break
        yield f"data: {json.dumps(line)}\n\n"


# =========================================================
# CAPTURE ROUTES
# =========================================================

@app.route("/capture/start", methods=["POST"])
def capture_start():
    global capture_proc

    with capture_lock:
        if capture_proc and capture_proc.poll() is None:
            return jsonify({"ok": False, "error": "Capture already running"}), 400

        body = request.get_json(silent=True) or {}

        env = os.environ.copy()
        env["ESP32_IP"]      = body.get("esp32_ip",      config["esp32_ip"])
        env["DEVICE"]        = body.get("camera_device",  config["camera_device"])
        env["SAVE_DIR"]      = body.get("save_dir",       config["save_dir"])
        env["FRAME_WIDTH"]   = str(body.get("frame_width",  config["frame_width"]))
        env["FRAME_HEIGHT"]  = str(body.get("frame_height", config["frame_height"]))
        env["RELAY_ID"]      = str(body.get("relay_id",     config["relay_id"]))

        # Patch main2.py to read from env at runtime via wrapper
        script = os.path.join(SCRIPT_DIR, "main2.py")

        capture_proc = subprocess.Popen(
            ["python3", "-u", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            env=env,
        )

        # Drain old queue
        while not capture_log_queue.empty():
            try:
                capture_log_queue.get_nowait()
            except queue.Empty:
                break

        t = threading.Thread(
            target=stream_output,
            args=(capture_proc, capture_log_queue),
            daemon=True,
        )
        t.start()

    return jsonify({"ok": True, "pid": capture_proc.pid})


@app.route("/capture/stop", methods=["POST"])
def capture_stop():
    global capture_proc
    with capture_lock:
        if capture_proc and capture_proc.poll() is None:
            capture_proc.terminate()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "No capture running"})


@app.route("/capture/status")
def capture_status():
    with capture_lock:
        if capture_proc is None:
            return jsonify({"running": False})
        running = capture_proc.poll() is None
        return jsonify({"running": running, "pid": capture_proc.pid if running else None})


@app.route("/capture/logs")
def capture_logs():
    return Response(
        sse_stream(capture_log_queue),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =========================================================
# ODM ROUTES
# =========================================================

@app.route("/odm/start", methods=["POST"])
def odm_start():
    global odm_proc

    with odm_lock:
        if odm_proc and odm_proc.poll() is None:
            return jsonify({"ok": False, "error": "ODM already running"}), 400

        body = request.get_json(silent=True) or {}

        input_dir  = body.get("odm_input",  config["odm_input"])
        output_dir = body.get("odm_output", config["odm_output"])
        node_host  = body.get("node_host",  config["node_host"])
        node_port  = str(body.get("node_port", config["node_port"]))

        script = os.path.join(SCRIPT_DIR, "run_odm.sh")

        env = os.environ.copy()
        env["NODE_HOST"] = node_host
        env["NODE_PORT"] = node_port

        odm_proc = subprocess.Popen(
            ["bash", script, input_dir, output_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            env=env,
        )

        while not odm_log_queue.empty():
            try:
                odm_log_queue.get_nowait()
            except queue.Empty:
                break

        t = threading.Thread(
            target=stream_output,
            args=(odm_proc, odm_log_queue),
            daemon=True,
        )
        t.start()

    return jsonify({"ok": True, "pid": odm_proc.pid})


@app.route("/odm/stop", methods=["POST"])
def odm_stop():
    global odm_proc
    with odm_lock:
        if odm_proc and odm_proc.poll() is None:
            odm_proc.terminate()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "No ODM process running"})


@app.route("/odm/status")
def odm_status():
    with odm_lock:
        if odm_proc is None:
            return jsonify({"running": False})
        running = odm_proc.poll() is None
        return jsonify({"running": running, "pid": odm_proc.pid if running else None})


@app.route("/odm/logs")
def odm_logs():
    return Response(
        sse_stream(odm_log_queue),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/odm/node-status")
def odm_node_status():
    """Proxy NodeODM task status so the frontend can poll it."""
    import urllib.request
    import urllib.error

    task_uuid = request.args.get("uuid", "")
    node_host = request.args.get("host", config["node_host"])
    node_port = request.args.get("port", str(config["node_port"]))

    if not task_uuid:
        return jsonify({"error": "uuid required"}), 400

    url = f"http://{node_host}:{node_port}/task/{task_uuid}/info"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# =========================================================
# CONFIG ROUTES
# =========================================================

@app.route("/config", methods=["GET"])
def get_config():
    return jsonify(config)


@app.route("/config", methods=["POST"])
def set_config():
    body = request.get_json(silent=True) or {}
    for key in config:
        if key in body:
            config[key] = body[key]
    return jsonify({"ok": True, "config": config})


# =========================================================
# DATASET ROUTE
# =========================================================

@app.route("/dataset/count")
def dataset_count():
    d = config["save_dir"]
    try:
        files = [
            f for f in os.listdir(d)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        ]
        return jsonify({"count": len(files), "dir": d})
    except Exception as e:
        return jsonify({"count": 0, "dir": d, "error": str(e)})


# =========================================================
# VIEWER ROUTES
# =========================================================

OUTPUTS_BASE = os.path.dirname(SCRIPT_DIR)  # ~/P/PERSEPSI/


@app.route("/outputs")
def list_outputs():
    """Return all output folders that contain the textured OBJ."""
    results = []
    try:
        for name in sorted(os.listdir(OUTPUTS_BASE), reverse=True):
            folder = os.path.join(OUTPUTS_BASE, name)
            obj = os.path.join(folder, "odm_texturing", "odm_textured_model_geo.obj")
            if os.path.isdir(folder) and os.path.isfile(obj):
                results.append(name)
    except Exception:
        pass
    return jsonify(results)


@app.route("/output-file/<folder>/<path:filename>")
def serve_output_file(folder, filename):
    """Serve any file inside an output folder (OBJ, MTL, textures)."""
    base = os.path.realpath(OUTPUTS_BASE)
    target = os.path.realpath(os.path.join(base, folder))
    # Guard against path traversal
    if not target.startswith(base + os.sep):
        return "Forbidden", 403
    return send_from_directory(target, filename)


# =========================================================
# MAIN
# =========================================================

@app.route("/")
def index():
    return render_template("index.html", config=config)


@app.route("/viewer")
def viewer():
    return render_template("viewer.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=12345, debug=False, threaded=True)
