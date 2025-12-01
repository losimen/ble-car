import asyncio
import os
import time
from enum import Enum
from bleak import BleakScanner, BleakClient
from dotenv import load_dotenv

# Load environment variables (SERVICE_UUID, CHARACTERISTIC_UUID)
load_dotenv()

# --- Configuration Constants from .env ---
SERVICE_UUID = os.getenv("SERVICE_UUID")
CHARACTERISTIC_UUID = os.getenv("CHARACTERISTIC_UUID")

if not SERVICE_UUID or not CHARACTERISTIC_UUID:
    print("FATAL: Please ensure SERVICE_UUID and CHARACTERISTIC_UUID are set in your .env file.")
    exit(1)

# --- Enums for Simplified Control ---

class CarMove(Enum):
    """
    Defines the basic movement commands for the car.
    """
    STOP = "%"
    FRONT = "%W"
    BACK = "%S"
    LEFT = "%L"
    RIGHT = "%R"

class BleCarDriver:
    """
    A class to handle BLE connection and command sending for a remote-controlled car.
    It manages the connection to a specific BLE device (e.g., JDY-16) and sends 
    movement and speed commands asynchronously.
    """
    def __init__(self, device_name="JDY-16", initial_speed=15):
        self._device_name = device_name
        self._current_speed = max(0, min(100, initial_speed))
        self._ble_client = None
        self._command_queue = asyncio.Queue()
        self._command_processor_task = None
        self._last_command = None
        self._is_connecting = False
        print(f"BleCarDriver initialized for device: {self._device_name}")

    # --- Connection Management ---

    async def _scan_for_device(self):
        """Scans for the specified BLE device and returns its address."""
        print(f"Scanning for BLE device '{self._device_name}'...")
        devices = await BleakScanner.discover(timeout=5.0)
        
        selected_device = next(
            (device for device in devices if self._device_name in (device.name or "")),
            None
        )

        if selected_device:
            print(f"Found device: {selected_device.name} ({selected_device.address})")
            return selected_device.address
        else:
            print(f"Device '{self._device_name}' not found.")
            return None

    async def connect(self):
        """
        Connects to the specified BLE device and starts the command processor task.
        Returns True on successful connection, False otherwise.
        """
        if self.is_connected:
            print("Already connected.")
            return True
        
        if self._is_connecting:
            print("Connection attempt already in progress.")
            return False
            
        self._is_connecting = True
        
        try:
            address = await self._scan_for_device()
            if not address:
                return False

            self._ble_client = BleakClient(address)
            await self._ble_client.connect()
            
            if self._ble_client.is_connected:
                print("Connected to BLE device!")
                
                # Check for required service
                service_found = any(service.uuid.upper() == SERVICE_UUID.upper() for service in self._ble_client.services)
                if not service_found:
                    print(f"Service with UUID {SERVICE_UUID} not found on device.")
                    await self._ble_client.disconnect()
                    self._ble_client = None
                    return False
                
                # Start the command processing loop
                self._command_processor_task = asyncio.create_task(self._command_processor())
                
                # Send initial speed command
                self.set_speed(self._current_speed)
                
                return True
            else:
                print("Failed to establish BLE connection.")
                return False

        except Exception as e:
            print(f"An error occurred during connection: {e}")
            self._ble_client = None
            return False
        finally:
            self._is_connecting = False

    async def disconnect(self):
        """Disconnects the BLE client and stops the command processor task."""
        if self._command_processor_task:
            self._command_processor_task.cancel()
        if self._ble_client and self._ble_client.is_connected:
            await self._ble_client.disconnect()
            print("Disconnected from BLE device.")
        self._ble_client = None

    @property
    def is_connected(self):
        """Returns True if the BLE client is connected."""
        return self._ble_client and self._ble_client.is_connected

    # --- Command Processing ---

    def _enqueue_command(self, command: str):
        print("CMD: SET: ", command)
        """Puts a command into the queue to be sent asynchronously."""
        try:
            self._command_queue.put_nowait(command)
        except asyncio.QueueFull:
            print(f"Queue full, dropping command: {command}")

    async def _command_processor(self):
        """
        Asynchronously waits for commands and writes them to the BLE characteristic.
        This task runs continuously while connected.
        """
        print('start processor')
        while True:
            try:
                command = await self._command_queue.get()
                print("hi", command, self.is_connected)
                if self.is_connected:
                    print('connected')
                    # Write command to the characteristic
                    await self._ble_client.write_gatt_char(
                        CHARACTERISTIC_UUID, 
                        command.encode(), 
                        response=False
                    )
                    print('ssss')
                    print('self last', self._last_command)
                    if command != self._last_command:
                        print(f"Sent: {command}")
                        self._last_command = command
                        
                self._command_queue.task_done()
            except asyncio.CancelledError:
                # Task was cancelled, exit the loop
                break
            except Exception as e:
                print(f"Error in command processor: {e}")
                # Wait a bit before trying to process the next command
                await asyncio.sleep(0.1)

    # --- Public Control Methods ---

    def move(self, direction: CarMove):
        """
        Sends a movement command to the car.
        
        Args:
            direction (CarMove): The desired direction (e.g., CarMove.FRONT).
        """
        if not self.is_connected:
            # Optionally raise an exception or log a warning
            print("Warning: Not connected. Cannot send move command.")
            return

        print('RCVD: ', direction)
        print('V: ', direction.value)
        # Note: The original code handles 'w' and 's' simultaneously 
        # by defaulting to the reverse of the 'reversed_on' flag.
        # Here we only handle single, direct commands from the Enum.
        self._enqueue_command(direction.value)

    def set_speed(self, speed: int):
        """
        Sets the global speed of the car (0-100).
        This command is typically sent before any movement command.
        
        Args:
            speed (int): The desired speed, clamped between 0 and 100.
        """
        new_speed = max(0, min(100, speed))
        
        if new_speed != self._current_speed:
            self._current_speed = new_speed
            speed_cmd = f"%{self._current_speed}-"
            self._enqueue_command(speed_cmd)
            print(f"Speed set to: {self._current_speed}")
        
        # Always send the speed command even if it's the same, 
        # to ensure the car's state is updated if needed.
        speed_cmd = f"%{self._current_speed}-"
        self._enqueue_command(speed_cmd)
    
    def get_current_speed(self):
        """Returns the currently set speed."""
        return self._current_speed
