import asyncio
import atexit
import threading
import time
import numpy as np
import json
from enum import Enum
from flask import Flask, jsonify, send_from_directory, request
from rtl_sdr_driver import RtlSdrDriver
from ble_car_driver import BleCarDriver, CarMove

import os
WEB_DIR = os.path.join(os.path.dirname(__file__), 'web')
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
app = Flask(__name__, static_folder=WEB_DIR)

# --- Config File Management ---
def load_config():
    """Load configuration from config.json file."""
    default_config = {'rotation_duration': 0.3, 'speed': 15}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Merge with defaults to ensure all keys exist
                return {**default_config, **config}
    except Exception as e:
        print(f"Error loading config: {e}")
    return default_config

def save_config(config):
    """Save configuration to config.json file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

# Global state to share data between the main Flask thread and background tasks
global_state = {
    'car_connected': False,
    'sdr_ready': False,
    'detection_running': False,
    'detection_results': {}, # {angle: power_dB, ...}
    'current_angle': 0, # Current simulated rotation angle
}

# Driver instances
car_driver = None
sdr_driver = None
DETECTION_THREAD = None

# Dedicated event loop for BLE operations (runs in its own thread)
ble_loop = None
ble_thread = None
ble_loop_ready = threading.Event()  # Synchronization primitive

def start_ble_event_loop():
    """Runs the dedicated BLE event loop in a background thread."""
    global ble_loop
    ble_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ble_loop)
    ble_loop_ready.set()  # Signal that loop is ready
    print("BLE event loop started and ready")
    ble_loop.run_forever()

def ensure_ble_loop_running():
    """Ensures BLE event loop thread is running. Call before any BLE operation."""
    global ble_thread
    if ble_thread is None or not ble_thread.is_alive():
        ble_loop_ready.clear()
        ble_thread = threading.Thread(target=start_ble_event_loop, daemon=True, name="BLE-EventLoop")
        ble_thread.start()
        # Wait for loop to actually be running (max 5s)
        if not ble_loop_ready.wait(timeout=5.0):
            raise RuntimeError("BLE event loop failed to start")
        print(f"BLE thread started: {ble_thread.name}, alive={ble_thread.is_alive()}")

def run_in_ble_loop(coro):
    """Schedule a coroutine in the BLE thread's event loop and wait for result."""
    ensure_ble_loop_running()
    if ble_loop is None or not ble_loop.is_running():
        raise RuntimeError("BLE event loop is not running")
    future = asyncio.run_coroutine_threadsafe(coro, ble_loop)
    return future.result(timeout=30)  # 30s timeout for BLE operations

def shutdown_ble():
    """Gracefully shutdown BLE: disconnect car, stop event loop, join thread."""
    global ble_loop, ble_thread, car_driver
    
    print("Shutting down BLE...")
    
    # Disconnect car if connected
    if car_driver and car_driver.is_connected:
        try:
            run_in_ble_loop(car_driver.disconnect())
        except Exception as e:
            print(f"Error disconnecting car: {e}")
    
    # Stop the event loop
    if ble_loop and ble_loop.is_running():
        ble_loop.call_soon_threadsafe(ble_loop.stop)
    
    # Wait for thread to finish
    if ble_thread and ble_thread.is_alive():
        ble_thread.join(timeout=2.0)
        print(f"BLE thread joined: alive={ble_thread.is_alive()}")
    
    ble_loop = None
    ble_thread = None
    print("BLE shutdown complete")

atexit.register(shutdown_ble)

# --- Configuration ---
WATCH_FREQ_MHZ = 433.4
SAMPLE_RATE_HZ = 1024000
ROTATION_STEP_DEGREES = 30 # How many degrees to turn per step in detection cycle
MEASUREMENT_TIME_SECONDS = 0.5 # How long to measure at each position
TOTAL_STEPS = 360 // ROTATION_STEP_DEGREES

# ====================================================================
# 3. BACKGROUND TASK LOGIC (Detection Cycle)
# ====================================================================

def run_detection_cycle():
    """
    Background function run in a separate thread to handle the
    synchronous SDR watch and asynchronous car move operations.
    """
    global global_state, car_driver, sdr_driver
    
    if not car_driver.is_connected or not sdr_driver:
        print("ERROR: Drivers not ready for detection.")
        global_state['detection_running'] = False
        return

    print("--- STARTING DETECTION CYCLE ---")
    global_state['detection_results'] = {}
    
    # Use a loop to perform the 360-degree scan
    for step in range(TOTAL_STEPS):
        if not global_state['detection_running']:
            break # Stop if requested

        current_angle = step * ROTATION_STEP_DEGREES
        global_state['current_angle'] = current_angle
        print(f"Detection: Step {step+1}/{TOTAL_STEPS} at {current_angle}°")

        # 1. Car Movement: Rotate to the new position (using saved rotation_duration)
        config = load_config()
        rotation_duration = config.get('rotation_duration', 0.3)
        try:
            run_in_ble_loop(async_move_and_wait(CarMove.RIGHT, rotation_duration))
        except Exception as e:
            print(f"BLE ERROR during move: {e}")
            global_state['detection_running'] = False
            break

        # 2. SDR Measurement: Watch for a specific time
        readings = []
        start_time = time.time()
        while time.time() - start_time < MEASUREMENT_TIME_SECONDS:
            power = sdr_driver.watch()
            readings.append(power)
        
        # 3. Process and Store Result
        if readings:
            avg_power = np.mean(readings)
            global_state['detection_results'][current_angle] = round(avg_power, 2)
            print(f"Result at {current_angle}°: {avg_power:.2f} dB")
        
        time.sleep(0.1) # Brief pause before next step

    global_state['detection_running'] = False
    print("--- DETECTION CYCLE COMPLETE ---")

async def async_move_and_wait(direction, duration):
    """Helper to run async car commands."""
    car_driver.move(direction)
    await asyncio.sleep(duration)
    car_driver.move(CarMove.STOP)


# ====================================================================
# 4. FLASK ROUTES
# ====================================================================

@app.route('/api/init_drivers', methods=['POST'])
def init_drivers():
    """Initializes and connects the car and SDR drivers."""
    global car_driver, sdr_driver, global_state
    
    try:
        # Ensure BLE event loop is running (handles thread creation/restart)
        ensure_ble_loop_running()
        
        # Initialize SDR (synchronous)
        sdr_driver = RtlSdrDriver(WATCH_FREQ_MHZ, SAMPLE_RATE_HZ, 0)
        global_state['sdr_ready'] = True

        # Initialize and connect Car (in the dedicated BLE event loop)
        car_driver = BleCarDriver()
        connect_success = run_in_ble_loop(car_driver.connect())
        
        if connect_success:
            global_state['car_connected'] = True
            return jsonify({'status': 'success', 'message': 'Drivers initialized and car connected.'})
        else:
            global_state['sdr_ready'] = False
            return jsonify({'status': 'error', 'message': 'SDR initialized, but failed to connect car.'})
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Initialization failed: {e}'})


@app.route('/api/move/<direction>', methods=['POST'])
def move_car(direction):
    """Handles WASD control commands."""
    global car_driver
    
    if not global_state['car_connected']:
        return jsonify({'status': 'error', 'message': 'Car not connected.'})
        
    try:
        move_command = CarMove[direction.upper()]
        run_in_ble_loop(async_move_and_wait(move_command, 1))
        
        return jsonify({'status': 'success', 'message': f'Car moved {direction}.'})
    except KeyError:
        return jsonify({'status': 'error', 'message': 'Invalid direction.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/speed/<int:value>', methods=['POST'])
def set_speed(value):
    """Sets car speed (0-100)."""
    global car_driver
    
    if not global_state['car_connected']:
        return jsonify({'status': 'error', 'message': 'Car not connected.'})
    
    try:
        car_driver.set_speed(value)
        # Save speed to config file
        config = load_config()
        config['speed'] = value
        save_config(config)
        return jsonify({'status': 'success', 'speed': car_driver.get_current_speed()})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/speed', methods=['GET'])
def get_speed():
    """Gets current car speed."""
    if not car_driver:
        return jsonify({'status': 'error', 'speed': 0})
    return jsonify({'status': 'success', 'speed': car_driver.get_current_speed()})


@app.route('/api/detect/start', methods=['POST'])
def start_detection():
    """Starts the signal detection background thread."""
    global global_state, DETECTION_THREAD
    
    if global_state['detection_running']:
        return jsonify({'status': 'error', 'message': 'Detection is already running.'})
        
    if not global_state['car_connected'] or not global_state['sdr_ready']:
        return jsonify({'status': 'error', 'message': 'Drivers are not ready. Initialize first.'})

    global_state['detection_running'] = True
    global_state['detection_results'] = {}
    
    # Start the detection loop in a new thread to avoid blocking Flask
    DETECTION_THREAD = threading.Thread(target=run_detection_cycle)
    DETECTION_THREAD.start()
    
    return jsonify({'status': 'running', 'message': 'Detection cycle started in background.'})


@app.route('/api/calibrate', methods=['POST'])
def calibrate():
    """Rotates the car for a specified duration (in seconds) for calibration purposes."""
    global car_driver
    
    if not global_state['car_connected']:
        return jsonify({'status': 'error', 'message': 'Car not connected.'})
    
    try:
        data = request.get_json()
        duration = float(data.get('duration', 3))
        save_to_config = data.get('save', True)  # By default, save to config
        
        if duration <= 0 or duration > 30:
            return jsonify({'status': 'error', 'message': 'Duration must be between 0 and 30 seconds.'})
        
        # Rotate the car to the right for the specified duration (same as triangulation)
        run_in_ble_loop(async_move_and_wait(CarMove.RIGHT, duration))
        
        # Save rotation_duration to config file if requested
        if save_to_config:
            config = load_config()
            config['rotation_duration'] = duration
            save_config(config)
        
        return jsonify({'status': 'success', 'message': f'Calibration rotation completed for {duration} seconds.', 'saved': save_to_config})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/detect/status', methods=['GET'])
def get_detection_status():
    """Endpoint for the frontend to poll for status and results."""
    current_db = None
    if sdr_driver and global_state['sdr_ready']:
        try:
            current_db = round(sdr_driver.watch(), 2)
        except:
            pass
    
    config = load_config()
    return jsonify({
        'running': global_state['detection_running'],
        'results': global_state['detection_results'],
        'car_connected': global_state['car_connected'],
        'sdr_ready': global_state['sdr_ready'],
        'current_db': current_db,
        'rotation_duration': config.get('rotation_duration', 0.3)
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Returns the saved configuration."""
    config = load_config()
    return jsonify({'status': 'success', 'config': config})


@app.route('/')
def index():
    """Serves the main HTML page."""
    return send_from_directory(WEB_DIR, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    """Serves static files (JS, CSS, etc.)."""
    return send_from_directory(WEB_DIR, filename)

if __name__ == '__main__':
    # Start the Flask app
    # host='0.0.0.0' allows access from other devices on the network
    app.run(host='0.0.0.0', port=5000, debug=True)