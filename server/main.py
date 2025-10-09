# main.py
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
EMA_ALPHA = 0.2                # smoothing factor for EMA (0.1-0.3 recommended)
BROADCAST_HZ = 15.0           # UI update frequency
BROADCAST_INTERVAL = 1.0 / BROADCAST_HZ
MAX_RAW = 2000                # keep this many raw samples per axis
MAX_SMOOTH_HISTORY = 400      # keep this many smoothed samples for plotting
FFT_N = 256                   # FFT window size
FFT_SEND_BINS = 128           # how many FFT bins to send to UI

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
    "baseline_fft": None,
    "fft_magnitude": []
})

dashboard_clients = set()
_last_broadcast = 0.0

# ---------------- Helpers ----------------
def to_serializable(obj):
    """Convert numpy arrays/scalars and nested lists to plain Python types for JSON."""
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
    """Append raw values (for FFT) and update EMA history lists for plotting."""
    machine = machines[machine_id]

    # append raw
    machine["x_buffer"].extend([float(v) for v in new_x_list])
    machine["y_buffer"].extend([float(v) for v in new_y_list])
    machine["z_buffer"].extend([float(v) for v in new_z_list])

    # keep raw limited
    machine["x_buffer"] = machine["x_buffer"][-MAX_RAW:]
    machine["y_buffer"] = machine["y_buffer"][-MAX_RAW:]
    machine["z_buffer"] = machine["z_buffer"][-MAX_RAW:]

    # EMA per sample (store smoothed values)
    for nx, ny, nz in zip(new_x_list, new_y_list, new_z_list):
        # X
        if not machine["x_smooth"]:
            machine["x_smooth"].append(float(nx))
        else:
            prev = machine["x_smooth"][-1]
            machine["x_smooth"].append(prev + EMA_ALPHA * (float(nx) - prev))
        # Y
        if not machine["y_smooth"]:
            machine["y_smooth"].append(float(ny))
        else:
            prev = machine["y_smooth"][-1]
            machine["y_smooth"].append(prev + EMA_ALPHA * (float(ny) - prev))
        # Z
        if not machine["z_smooth"]:
            machine["z_smooth"].append(float(nz))
        else:
            prev = machine["z_smooth"][-1]
            machine["z_smooth"].append(prev + EMA_ALPHA * (float(nz) - prev))

    # limit smooth history
    H = MAX_SMOOTH_HISTORY
    machine["x_smooth"] = machine["x_smooth"][-H:]
    machine["y_smooth"] = machine["y_smooth"][-H:]
    machine["z_smooth"] = machine["z_smooth"][-H:]

def compute_fft_magnitude(machine_id):
    """Compute FFT magnitude on the magnitude of x,y,z if enough raw samples."""
    machine = machines[machine_id]
    N = FFT_N
    if len(machine["x_buffer"]) >= N:
        x = np.array(machine["x_buffer"][-N:], dtype=float)
        y = np.array(machine["y_buffer"][-N:], dtype=float)
        z = np.array(machine["z_buffer"][-N:], dtype=float)
        magnitude = np.sqrt(x**2 + y**2 + z**2)

        fft_vals = np.abs(np.fft.fft(magnitude))
        machine["fft_magnitude"] = fft_vals  # stored as numpy array

        # Baseline/anomaly detection
        baseline = machine.get("baseline_fft")
        if baseline is None:
            machine["baseline_fft"] = fft_vals.copy()
            machine["status"] = "active"
            print(f"{machine_id}: baseline_fft established")
        else:
            try:
                deviation = np.linalg.norm(fft_vals - baseline)
            except Exception as e:
                deviation = float("inf")
                print("Error computing deviation:", e)
            if deviation > 50:
                machine["status"] = "critical"
            elif deviation > 20:
                machine["status"] = "warning"
            else:
                machine["status"] = "active"

async def broadcast_dashboard():
    """Send snapshot to all connected dashboard clients (converted to JSON-serializable)."""
    snapshot = {}
    for machine_id, machine in machines.items():
        fft_raw = machine.get("fft_magnitude", [])
        fft_for_json = to_serializable(fft_raw)
        snapshot[machine_id] = {
            "status": machine["status"],
            "x": to_serializable(machine["x_smooth"][-MAX_SMOOTH_HISTORY:]),
            "y": to_serializable(machine["y_smooth"][-MAX_SMOOTH_HISTORY:]),
            "z": to_serializable(machine["z_smooth"][-MAX_SMOOTH_HISTORY:]),
            "fft_magnitude": (fft_for_json[:FFT_SEND_BINS] if isinstance(fft_for_json, list) else fft_for_json)
        }

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
            # debug print
            print("Received from ESP32:", message)
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                print("Invalid JSON from ESP32:", message)
                continue

            machine_id = data.get("machine_id") or data.get("id")  # accept both keys
            if not machine_id:
                print("Message missing machine_id/id:", data)
                continue

            x_vals = data.get("x", [])
            y_vals = data.get("y", [])
            z_vals = data.get("z", [])

            if not (isinstance(x_vals, list) and isinstance(y_vals, list) and isinstance(z_vals, list)):
                print("x/y/z not lists, skipping:", type(x_vals), type(y_vals), type(z_vals))
                continue

            # Store raw + smoothed
            apply_ema_and_store(machine_id, x_vals, y_vals, z_vals)

            # Compute FFT on magnitude if enough samples
            compute_fft_magnitude(machine_id)

            # Send queued command to ESP if any
            if machines[machine_id]["command_queue"]:
                cmd = machines[machine_id]["command_queue"].pop(0)
                try:
                    await websocket.send_text(json.dumps({"command": cmd}))
                    print(f"Sent command to {machine_id}: {cmd}")
                except Exception as e:
                    print("Failed to send command:", e)

            # Throttle dashboard broadcasts
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
            await asyncio.sleep(1)  # keep connection alive
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
