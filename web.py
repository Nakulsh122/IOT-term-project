# server.py
import asyncio
import websockets
import json

connected_clients = set()

async def handler(websocket):
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"\nReceived from {data.get('machine_id')} - Type: {data.get('type')}")
            # Print first 10 samples for X/Y/Z
            if "x" in data and isinstance(data["x"], list):
                print("X:", data["x"][:10], "...")
                print("Y:", data["y"][:10], "...")
                print("Z:", data["z"][:10], "...")
            # Here you can run FFT / anomaly detection
    except Exception as e:
        print("Error:", e)
    finally:
        connected_clients.remove(websocket)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("WebSocket Server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

asyncio.run(main())
