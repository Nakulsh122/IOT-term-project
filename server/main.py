from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import json
import numpy as np
import time
from collections import defaultdict

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------- Configuration ----------------
EMA_ALPHA = 0.2                
BROADCAST_HZ = 15.0
BROADCAST_INTERVAL = 1.0 / BROADCAST_HZ
MAX_RAW = 2000
MAX_SMOOTH_HISTORY = 400

SAMPLE_RATE = 50.0            
FFT_N = 128                   
FFT_PAD_TO = 128              
FFT_SEND_BINS = 64            

# ---------------- Machine Storage ----------------
machines = defaultdict(lambda: {
    "x_buffer": [],
    "y_buffer": [],
    "z_buffer": [],
    "x_smooth": [],
    "y_smooth": [],
    "z_smooth": [],
    "status": "idle",
    "command_queue": [],
    # baseline fields (separate per-axis)
    "baseline_mean_db_x": None,
    "baseline_M2_db_x": None,
    "baseline_count_x": 0,
    "baseline_mean_db_y": None,
    "baseline_M2_db_y": None,
    "baseline_count_y": 0,
    "baseline_mean_db_z": None,
    "baseline_M2_db_z": None,
    "baseline_count_z": 0,
    "fft_freqs": None,
    "fft_x_db": None,
    "fft_y_db": None,
    "fft_z_db": None,
})

dashboard_clients = set()
_last_broadcast = 0.0

# ---------------- Helpers ----------------
def to_serializable(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, (np.integer, np.int32, np.int64, np.int_)):
        return int(obj)
    if isinstance(obj, list):
        return [to_serializable(x) for x in obj]
    return obj

def apply_ema_and_store(machine_id, new_x_list, new_y_list, new_z_list):
    machine = machines[machine_id]
    machine["x_buffer"].extend([float(v) for v in new_x_list])
    machine["y_buffer"].extend([float(v) for v in new_y_list])
    machine["z_buffer"].extend([float(v) for v in new_z_list])
    machine["x_buffer"] = machine["x_buffer"][-MAX_RAW:]
    machine["y_buffer"] = machine["y_buffer"][-MAX_RAW:]
    machine["z_buffer"] = machine["z_buffer"][-MAX_RAW:]

    for nx, ny, nz in zip(new_x_list, new_y_list, new_z_list):
        if not machine["x_smooth"]:
            machine["x_smooth"].append(float(nx))
        else:
            prev = machine["x_smooth"][-1]
            machine["x_smooth"].append(prev + EMA_ALPHA * (float(nx) - prev))
        if not machine["y_smooth"]:
            machine["y_smooth"].append(float(ny))
        else:
            prev = machine["y_smooth"][-1]
            machine["y_smooth"].append(prev + EMA_ALPHA * (float(ny) - prev))
        if not machine["z_smooth"]:
            machine["z_smooth"].append(float(nz))
        else:
            prev = machine["z_smooth"][-1]
            machine["z_smooth"].append(prev + EMA_ALPHA * (float(nz) - prev))

    H = MAX_SMOOTH_HISTORY
    machine["x_smooth"] = machine["x_smooth"][-H:]
    machine["y_smooth"] = machine["y_smooth"][-H:]
    machine["z_smooth"] = machine["z_smooth"][-H:]

def compute_fft_per_axis_and_compare(machine_id):
    """
    Compute per-axis FFTs (rFFT), convert to dB, update running baseline per-axis using Welford,
    and set machine status based on aggregated deviations.
    """
    machine = machines[machine_id]
    N = FFT_N
    if len(machine["x_buffer"]) < N:
        return

    # Latest N samples
    x = np.array(machine["x_buffer"][-N:], dtype=float)
    y = np.array(machine["y_buffer"][-N:], dtype=float)
    z = np.array(machine["z_buffer"][-N:], dtype=float)

    # for each axis: DC remove, window, pad, rfft, convert to dB
    def axis_fft_db(axis_arr):
        a = axis_arr - np.mean(axis_arr)
        w = np.hanning(N)
        aw = a * w
        pad_to = max(FFT_PAD_TO, N)
        padded = np.pad(aw, (0, pad_to - N), mode='constant')
        X = np.fft.rfft(padded)
        freqs = np.fft.rfftfreq(len(padded), d=1.0 / SAMPLE_RATE)
        gain = np.sum(w) / N
        mag_lin = np.abs(X) / (N * gain + 1e-12)
        mag_db = 20.0 * np.log10(np.maximum(mag_lin, 1e-12))
        return freqs, mag_db

    freqs, x_db = axis_fft_db(x)
    _, y_db = axis_fft_db(y)
    _, z_db = axis_fft_db(z)

    machine["fft_freqs"] = freqs
    machine["fft_x_db"] = x_db
    machine["fft_y_db"] = y_db
    machine["fft_z_db"] = z_db

    # Update baseline per-axis (Welford) and compute per-axis z-peaks
    def update_baseline_and_get_z(mag_db, mean_db_key, M2_key, count_key):
        mean = machine.get(mean_db_key)
        M2 = machine.get(M2_key)
        cnt = machine.get(count_key, 0)
        if mean is None:
            machine[mean_db_key] = np.array(mag_db, dtype=float)
            machine[M2_key] = np.zeros_like(mag_db, dtype=float)
            machine[count_key] = 1
            return 0.0  # no deviation on first baseline sample
        # update
        cnt += 1
        delta = mag_db - machine[mean_db_key]
        mean_new = machine[mean_db_key] + delta / cnt
        delta2 = mag_db - mean_new
        M2_new = machine[M2_key] + delta * delta2
        machine[mean_db_key] = mean_new
        machine[M2_key] = M2_new
        machine[count_key] = cnt
        std = np.sqrt(np.maximum(M2_new / np.maximum(cnt - 1, 1), 1e-6))
        z = np.abs((mag_db - mean_new) / std)
        return float(np.max(z))

    z_x = update_baseline_and_get_z(x_db, "baseline_mean_db_x", "baseline_M2_db_x", "baseline_count_x")
    z_y = update_baseline_and_get_z(y_db, "baseline_mean_db_y", "baseline_M2_db_y", "baseline_count_y")
    z_z = update_baseline_and_get_z(z_db, "baseline_mean_db_z", "baseline_M2_db_z", "baseline_count_z")

    # aggregate deviations to a single status (simple rule: if any axis critical -> critical)
    # thresholds tunable
    status = "active"
    if z_x > 4.0 or z_y > 4.0 or z_z > 4.0:
        status = "critical"
    elif z_x > 2.0 or z_y > 2.0 or z_z > 2.0:
        status = "warning"

    machine["status"] = status

def prepare_snapshot():
    snapshot = {}
    for machine_id, machine in machines.items():
        freqs = machine.get("fft_freqs")
        if freqs is not None:
            max_bins = min(len(freqs), FFT_SEND_BINS)
            freqs_send = freqs[:max_bins].tolist()
            x_send = machine["fft_x_db"][:max_bins].tolist()
            y_send = machine["fft_y_db"][:max_bins].tolist()
            z_send = machine["fft_z_db"][:max_bins].tolist()
        else:
            freqs_send = []
            x_send = []
            y_send = []
            z_send = []

        snapshot[machine_id] = {
            "status": machine["status"],
            "x": to_serializable(machine["x_smooth"][-MAX_SMOOTH_HISTORY:]),
            "y": to_serializable(machine["y_smooth"][-MAX_SMOOTH_HISTORY:]),
            "z": to_serializable(machine["z_smooth"][-MAX_SMOOTH_HISTORY:]),
            "fft_freqs": freqs_send,
            "fft_x_db": x_send,
            "fft_y_db": y_send,
            "fft_z_db": z_send
        }
    return snapshot

async def broadcast_dashboard():
    snapshot = prepare_snapshot()
    payload = json.dumps(snapshot)
    for client in list(dashboard_clients):
        try:
            await client.send_text(payload)
        except Exception:
            try:
                dashboard_clients.remove(client)
            except KeyError:
                pass

# ---------------- Routes & WebSockets ----------------
@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws/esp")
async def websocket_esp(websocket: WebSocket):
    await websocket.accept()
    print("ESP32 connected")
    machine_id = None
    global _last_broadcast
    try:
        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                print("Invalid JSON from ESP32:", message)
                continue

            machine_id = data.get("machine_id") or data.get("id")
            if not machine_id:
                print("Message missing machine_id/id:", data)
                continue

            x_vals = data.get("x", [])
            y_vals = data.get("y", [])
            z_vals = data.get("z", [])

            if not (isinstance(x_vals, list) and isinstance(y_vals, list) and isinstance(z_vals, list)):
                print("x/y/z not lists, skipping.")
                continue

            apply_ema_and_store(machine_id, x_vals, y_vals, z_vals)
            compute_fft_per_axis_and_compare(machine_id)

            if machines[machine_id]["command_queue"]:
                cmd = machines[machine_id]["command_queue"].pop(0)
                try:
                    await websocket.send_text(json.dumps({"command": cmd}))
                    print(f"Sent command to {machine_id}: {cmd}")
                except Exception as e:
                    print("Failed to send command:", e)

            now = time.time()
            if now - _last_broadcast >= BROADCAST_INTERVAL:
                await broadcast_dashboard()
                _last_broadcast = now

    except WebSocketDisconnect:
        print(f"{machine_id} disconnected")
    except Exception as e:
        print("Exception in websocket_esp:", e)

@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await websocket.accept()
    dashboard_clients.add(websocket)
    print("Dashboard connected (clients):", len(dashboard_clients))
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        dashboard_clients.remove(websocket)
        print("Dashboard disconnected (clients):", len(dashboard_clients))

@app.post("/command")
async def send_command(request: Request):
    data = await request.json()
    machine_id = data.get("id")
    command = data.get("command")
    if machine_id in machines:
        machines[machine_id]["command_queue"].append(command)
        print(f"Queued command for {machine_id}: {command}")
        return {"status": "ok"}
    return {"status": "machine not found"}
