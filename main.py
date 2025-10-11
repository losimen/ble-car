import asyncio
import tkinter as tk
from bleak import BleakScanner, BleakClient
import os
from dotenv import load_dotenv

load_dotenv()

SERVICE_UUID = os.getenv("SERVICE_UUID")
CHARACTERISTIC_UUID = os.getenv("CHARACTERISTIC_UUID")

keys_pressed = set()
last_command = None
reversed_on = False
current_speed = 50
ble_client = None
root = None
label = None
command_sender_task = None
command_queue = asyncio.Queue()

def get_command():
    if 'w' in keys_pressed and 's' in keys_pressed:
        return "%S" if reversed_on else "%W"
    elif 'w' in keys_pressed:
        return "%W"
    elif 's' in keys_pressed:
        return "%S"
    elif 'a' in keys_pressed:
        return "%L"
    elif 'd' in keys_pressed:
        return "%R"
    else:
        return "%"

async def continuous_command_sender():
    while True:
        try:
            current_command = get_command()
            send_command_sync(current_command)
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(0.1)

def update_display():
    keys_text = "Keys currently pressed:\n" + ", ".join(sorted(keys_pressed)).upper() if keys_pressed else "Keys currently pressed:\nNone"
    speed_text = f"Current speed: {current_speed}"
    display_text = keys_text + "\n\n" + speed_text
    label.config(text=display_text)

async def command_processor():
    global ble_client, last_command
    while True:
        try:
            command = await command_queue.get()
            if ble_client and ble_client.is_connected:
                try:
                    await ble_client.write_gatt_char(CHARACTERISTIC_UUID, command.encode())
                    if command != last_command:
                        print(f"Sent: {command}")
                        last_command = command
                except Exception as e:
                    print(f"Error sending command '{command}': {e}")
            command_queue.task_done()
        except Exception as e:
            print(f"Command processor error: {e}")
            await asyncio.sleep(0.1)

def send_command_sync(command):
    try:
        command_queue.put_nowait(command)
    except asyncio.QueueFull:
        print(f"Queue full, dropping command: {command}")


def on_key_press(event):
    key = event.keysym.lower()
    if not key or key == '??':
        return

    if key in ['w', 'a', 's', 'd']:
        if key not in keys_pressed:
            keys_pressed.add(key)
            update_display()

def on_key_release(event):
    key = event.keysym.lower()
    if not key or key == '??':
        return

    if key in ['w', 'a', 's', 'd']:
        if key in keys_pressed:
            keys_pressed.discard(key)
            update_display()

def on_key_down_arrow(event):
    global reversed_on
    reversed_on = not reversed_on
    update_display()

def on_key_right_arrow(event):
    global current_speed
    new_speed = min(current_speed + 5, 100)
    if new_speed != current_speed:
        current_speed = new_speed
        update_display()
        speed_cmd = f"%{current_speed}-"
        send_command_sync(speed_cmd)

def on_key_left_arrow(event):
    global current_speed
    new_speed = max(current_speed - 5, 0)
    if new_speed != current_speed:
        current_speed = new_speed
        update_display()
        speed_cmd = f"%{current_speed}-"
        send_command_sync(speed_cmd)

async def tk_mainloop(window, interval=0.01):
    while True:
        try:
            window.update()
        except tk.TclError:
            break
        await asyncio.sleep(interval)

async def scan_and_connect():
    global ble_client, root, label, command_sender_task

    print("Scanning for BLE devices...")

    devices_dict = {}

    def detection_callback(device, advertisement_data):
        devices_dict[device.address] = (device, advertisement_data.rssi)

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(5.0)
    await scanner.stop()

    devices = list(devices_dict.values())

    if not devices:
        print("No devices found.")
        return

    selected_device = None
    for device, rssi in devices:
        if "JDY-16" in (device.name or ""):
            selected_device = device
            break

    if selected_device is None:
        print("JDY-16 not found.")
        return

    print(f"Connecting to {selected_device.name}...")

    try:
        async with BleakClient(selected_device) as client:
            ble_client = client
            if client.is_connected:
                print("Connected!")
                service_found = any(service.uuid == SERVICE_UUID for service in client.services)
                if not service_found:
                    print("Service not found.")
                    return

                command_processor_task = asyncio.create_task(command_processor())

                send_command_sync("%")

                command_sender_task = asyncio.create_task(continuous_command_sender())

                root = tk.Tk()
                root.title("WASD Command Sender")
                root.geometry("400x200")
                label = tk.Label(root, text=("Press WASD keys to control...\n"
                                             "W=Forward, S=Backward, A=Left, D=Right\n"
                                             "Arrow Keys: Down=Toggle Reverse, Left/Right=Adjust Speed"),
                                 font=("Helvetica", 14), justify="left")
                label.pack(expand=True, padx=20, pady=20)

                root.bind("<KeyPress>", on_key_press)
                root.bind("<KeyRelease>", on_key_release)
                root.bind("<Down>", on_key_down_arrow)
                root.bind("<Right>", on_key_right_arrow)
                root.bind("<Left>", on_key_left_arrow)
                root.focus_set()

                await tk_mainloop(root)
            else:
                print("Connection failed.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(scan_and_connect())
