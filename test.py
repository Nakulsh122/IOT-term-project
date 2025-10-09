import paho.mqtt.client as mqtt
import json

# ---------------------- MQTT Settings ----------------------
broker = "10.77.175.73"  # Replace with your MQTT broker IP
port = 1883
topic = "machine/sensor"

# ---------------------- Callback Functions ----------------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker!")
        client.subscribe(topic)
        print(f"Subscribed to topic: {topic}")
    else:
        print("Failed to connect, return code", rc)

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        data = json.loads(payload)
        print("\n--- New Message ---")
        print("Machine ID:", data.get("machine_id"))
        print("Type:", data.get("type"))
        # Check if array or single reading
        if "x" in data and isinstance(data["x"], list):
            print(f"X samples: {data['x'][:10]} ... total {len(data['x'])} samples")
            print(f"Y samples: {data['y'][:10]} ... total {len(data['y'])} samples")
            print(f"Z samples: {data['z'][:10]} ... total {len(data['z'])} samples")
        else:
            print("X:", data.get("x"), "Y:", data.get("y"), "Z:", data.get("z"))
    except Exception as e:
        print("Error parsing message:", e)
        print("Raw message:", msg.payload)

# ---------------------- Main ----------------------
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(broker, port, 60)
client.loop_forever()
