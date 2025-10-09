import json
import numpy as np
import pandas as pd
from scipy.fft import fft
import paho.mqtt.client as mqtt
import time

# -----------------
# MQTT Settings
# -----------------
BROKER = "localhost"
PORT = 1883
TOPIC_TELEMETRY = "machine/sensor"  # if using Mosquitto
TOPIC_COMMAND = "machine/command/"

# Machine thresholds
TEMPERATURE_THRESHOLD = 75.0
VIBRATION_THRESHOLD = 1.5

# Store baseline for moving average
machine_baseline_temp = {}

# -----------------
# Anomaly Detection
# -----------------
def detect_vibration_anomaly(vibration_data):
    """Compute FFT and detect spikes"""
    fft_vals = np.abs(fft(vibration_data))
    if np.max(fft_vals) > VIBRATION_THRESHOLD:
        return True
    return False

def detect_temperature_anomaly(machine_id, temp):
    """Simple moving average anomaly"""
    if machine_id not in machine_baseline_temp:
        machine_baseline_temp[machine_id] = temp
    avg_temp = machine_baseline_temp[machine_id]
    # Update baseline with low-pass effect
    machine_baseline_temp[machine_id] = 0.9*avg_temp + 0.1*temp
    return temp > TEMPERATURE_THRESHOLD

# -----------------
# MQTT Callbacks
# -----------------
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))
    client.subscribe(TOPIC_TELEMETRY)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        machine_id = data.get("machine_id", "MACHINE_1")
        temp = data["temperature"]
        vibration = data["vibration"]

        vib_alert = detect_vibration_anomaly(vibration)
        temp_alert = detect_temperature_anomaly(machine_id, temp)

        if vib_alert or temp_alert:
            print(f"[ALERT] {machine_id} -> Vibration: {vib_alert}, Temp: {temp_alert}")
            # Send alert/command back to ESP32
            alert_payload = json.dumps({"action":"shutdown"})
            client.publish(f"{TOPIC_COMMAND}{machine_id}", alert_payload)

        # Optional: log telemetry
        print(f"{machine_id} -> Temp: {temp}, Vibration RMS: {np.sqrt(np.mean(np.square(vibration)))}")

    except Exception as e:
        print("Error:", e)

# -----------------
# Run MQTT Client
# -----------------
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)
client.loop_forever()
