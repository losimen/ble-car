import asyncio
import tkinter as tk
from bleak import BleakScanner, BleakClient

# Replace these with the actual UUIDs for your BLE service and characteristic.
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ffe3-0000-1000-8000-00805f9b34fb"

# Global variables to track key state, last command, reversed flag, current speed,
# BLE client, and Tkinter window.
keys_pressed = set()
last_command = None
reversed_on = False  # Internal flag (affects command determination but is not shown as text)
current_speed = 50   # Default speed (can be adjusted with arrow keys)
ble_client = None    # Will hold the BleakClient instance.
root = None          # Tkinter window.
label = None         # Tkinter label for display.
command_sender_task = None  # Task for continuous command sending
command_queue = asyncio.Queue()  # Queue for all BLE commands

def get_command():
    """Determine command based on currently pressed WASD keys and reversed flag."""
    # W = forward, S = backward, A = left, D = right
    # If both W and S are pressed, decide command based on reversed_on
    if 'w' in keys_pressed and 's' in keys_pressed:
        return "%S" if reversed_on else "%W"
    # W pressed (forward)
    elif 'w' in keys_pressed:
        return "%W"
    # S pressed (backward)
    elif 's' in keys_pressed:
        return "%S"
    # A pressed (left)
    elif 'a' in keys_pressed:
        return "%L"
    # D pressed (right)
    elif 'd' in keys_pressed:
        return "%R"
    else:
        return "%"  # Default command when no relevant key is pressed

async def continuous_command_sender():
    """Continuously send the current command to prevent connection timeout."""
    while True:
        try:
            current_command = get_command()
            # Queue command every 100ms to maintain connection
            send_command_sync(current_command)
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(0.1)

def update_display():
    """Update the Tkinter label to show the current state and current speed."""
    keys_text = "Keys currently pressed:\n" + ", ".join(sorted(keys_pressed)).upper() if keys_pressed else "Keys currently pressed:\nNone"
    speed_text = f"Current speed: {current_speed}"
    display_text = keys_text + "\n\n" + speed_text
    label.config(text=display_text)

async def command_processor():
    """Process commands from the queue sequentially (blocking interface)."""
    global ble_client, last_command
    while True:
        try:
            command = await command_queue.get()
            if ble_client and ble_client.is_connected:
                try:
                    await ble_client.write_gatt_char(CHARACTERISTIC_UUID, command.encode())
                    # Only log when command changes
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
    """Synchronous helper to queue a command."""
    try:
        command_queue.put_nowait(command)
    except asyncio.QueueFull:
        print(f"Queue full, dropping command: {command}")


def on_key_press(event):
    """Handle WASD key press events."""
    # Use keysym instead of char to avoid issues with key repeat
    key = event.keysym.lower()
    # Filter out empty or invalid keysyms (happens with sticky keys)
    if not key or key == '??':
        return

    if key in ['w', 'a', 's', 'd']:
        # Only update if the key wasn't already pressed (avoid key repeat)
        if key not in keys_pressed:
            keys_pressed.add(key)
            update_display()

def on_key_release(event):
    """Handle WASD key release events."""
    # Use keysym instead of char to avoid issues with key repeat
    key = event.keysym.lower()
    # Filter out empty or invalid keysyms (happens with sticky keys)
    if not key or key == '??':
        return

    if key in ['w', 'a', 's', 'd']:
        # Only update if the key was actually pressed
        if key in keys_pressed:
            keys_pressed.discard(key)
            update_display()

def on_key_down_arrow(event):
    """Toggle reversed mode when the Down arrow key is pressed."""
    global reversed_on
    reversed_on = not reversed_on
    update_display()

def on_key_right_arrow(event):
    """Increase speed by 5 (max 100) when Right arrow key is pressed and send speed command."""
    global current_speed
    new_speed = min(current_speed + 5, 100)
    if new_speed != current_speed:
        current_speed = new_speed
        update_display()
        # Queue speed command through the blocking interface
        speed_cmd = f"%{current_speed}-"
        send_command_sync(speed_cmd)

def on_key_left_arrow(event):
    """Decrease speed by 5 (min 0) when Left arrow key is pressed and send speed command."""
    global current_speed
    new_speed = max(current_speed - 5, 0)
    if new_speed != current_speed:
        current_speed = new_speed
        update_display()
        # Queue speed command through the blocking interface
        speed_cmd = f"%{current_speed}-"
        send_command_sync(speed_cmd)

async def tk_mainloop(window, interval=0.01):
    """Integrate Tkinter's mainloop with asyncio."""
    while True:
        try:
            window.update()
        except tk.TclError:
            break  # Window has been closed.
        await asyncio.sleep(interval)

async def scan_and_connect():
    global ble_client, root, label, command_sender_task

    print("Scanning for BLE devices...")

    # Use a dictionary to store devices with their RSSI values
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

    # Ask the user to choose a device by index.
    '''
    while True:
        try:
            choice = input("\nEnter the number of the device to connect: ")
            idx_choice = int(choice)
            if 0 <= idx_choice < len(devices):
                break
            else:
                print("Invalid choice. Please enter a valid number from the list.")
        except ValueError:
            print("Invalid input. Please enter a number.")
    '''
    
    # selected_device = devices[idx_choice][0]

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
                # Services are automatically discovered on connection in newer bleak versions
                service_found = any(service.uuid == SERVICE_UUID for service in client.services)
                if not service_found:
                    print("Service not found.")
                    return

                # Start the command processor task (handles queue sequentially)
                command_processor_task = asyncio.create_task(command_processor())

                # Send the initial command (default "%") through the queue
                send_command_sync("%")

                # Start continuous command sender task
                command_sender_task = asyncio.create_task(continuous_command_sender())

                # Create and configure the Tkinter window.
                root = tk.Tk()
                root.title("WASD Command Sender")
                root.geometry("400x200")
                label = tk.Label(root, text=("Press WASD keys to control...\n"
                                             "W=Forward, S=Backward, A=Left, D=Right\n"
                                             "Arrow Keys: Down=Toggle Reverse, Left/Right=Adjust Speed"),
                                 font=("Helvetica", 14), justify="left")
                label.pack(expand=True, padx=20, pady=20)

                # Bind WASD key events.
                root.bind("<KeyPress>", on_key_press)
                root.bind("<KeyRelease>", on_key_release)
                # Bind arrow key events.
                root.bind("<Down>", on_key_down_arrow)
                root.bind("<Right>", on_key_right_arrow)
                root.bind("<Left>", on_key_left_arrow)
                # Ensure the window has focus to receive key events.
                root.focus_set()

                # Run the Tkinter mainloop integrated with asyncio.
                await tk_mainloop(root)
            else:
                print("Connection failed.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(scan_and_connect())
