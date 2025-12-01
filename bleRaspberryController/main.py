import asyncio
import atexit
import threading
import time
import numpy as np
import json
from enum import Enum
from flask import Flask, render_template_string, request, jsonify
from rtl_sdr_driver import RtlSdrDriver
from ble_car_driver import BleCarDriver, CarMove

app = Flask(__name__)

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

        # 1. Car Movement: Rotate to the new position
        try:
            run_in_ble_loop(async_move_and_wait(CarMove.RIGHT, ROTATION_STEP_DEGREES / 360 * 1.0))
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


@app.route('/api/detect/status', methods=['GET'])
def get_detection_status():
    """Endpoint for the frontend to poll for status and results."""
    return jsonify({
        'running': global_state['detection_running'],
        'results': global_state['detection_results'],
        'car_connected': global_state['car_connected'],
        'sdr_ready': global_state['sdr_ready']
    })


@app.route('/')
def index():
    """Renders the single-page web interface."""
    
    # The entire HTML, CSS (Tailwind), and JavaScript (for control and plotting)
    # is contained in this Python string.
    
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BLE Car & SDR Controller</title>
    <!-- Load Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'primary': '#4f46e5',
                        'secondary': '#10b981',
                        'dark-bg': '#1f2937',
                    }
                }
            }
        }
    </script>
    <style>
        /* Custom styles for the circular plot */
        #polarCanvas {
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.06);
        }
        .control-button {
            transition: all 0.1s ease-in-out;
            transform-origin: center;
        }
        .control-button:hover {
            box-shadow: 0 0 10px rgba(79, 70, 229, 0.7);
            transform: scale(1.05);
        }
        .control-button:active {
            transform: scale(0.95);
        }
    </style>
</head>
<body class="bg-gray-100 min-h-screen p-4 sm:p-8 font-sans">
    <div class="max-w-4xl mx-auto bg-white rounded-xl shadow-2xl p-6 lg:p-10">
        <h1 class="text-3xl font-bold text-center text-primary mb-2">Autonomous Signal Mapping Car</h1>
        <p class="text-center text-gray-600 mb-6">Control BLE Car and measure RTL-SDR signal strength.</p>

        <!-- Status and Initialization -->
        <div class="mb-8 p-4 border border-gray-200 rounded-lg shadow-inner bg-gray-50">
            <h2 class="text-xl font-semibold text-gray-700 mb-3">System Status</h2>
            <div id="statusIndicators" class="flex justify-around space-x-4 mb-4">
                <span id="carStatus" class="flex items-center text-sm font-medium text-red-600">
                    <svg class="w-4 h-4 mr-1 animate-pulse" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path></svg>
                    Car: Disconnected
                </span>
                <span id="sdrStatus" class="flex items-center text-sm font-medium text-red-600">
                    <svg class="w-4 h-4 mr-1 animate-pulse" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path></svg>
                    SDR: Not Ready
                </span>
            </div>
            <button id="initButton" onclick="initDrivers()" class="w-full bg-primary text-white py-2 rounded-lg font-bold hover:bg-indigo-600 focus:outline-none focus:ring-4 focus:ring-indigo-300 control-button">
                Initialize & Connect Drivers
            </button>
            <p id="initMessage" class="text-center mt-2 text-sm text-gray-500"></p>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
            
            <!-- Left Panel: Manual Control -->
            <div class="bg-dark-bg p-6 rounded-xl shadow-lg">
                <h2 class="text-2xl font-bold text-white mb-4 text-center">Manual Control (W/A/S/D)</h2>
                <div class="flex flex-col items-center space-y-4">
                    <!-- Forward -->
                    <button class="w-24 h-12 bg-primary text-white text-lg font-bold rounded-xl control-button" onclick="moveCar('front')">W</button>
                    <!-- Left/Right -->
                    <div class="flex space-x-8">
                        <button class="w-24 h-12 bg-primary text-white text-lg font-bold rounded-xl control-button" onclick="moveCar('left')">A</button>
                        <button class="w-24 h-12 bg-primary text-white text-lg font-bold rounded-xl control-button" onclick="moveCar('right')">D</button>
                    </div>
                    <!-- Backward -->
                    <button class="w-24 h-12 bg-primary text-white text-lg font-bold rounded-xl control-button" onclick="moveCar('backward')">S</button>
                </div>
            </div>

            <!-- Right Panel: Signal Detection -->
            <div class="bg-white p-6 rounded-xl border border-gray-200 shadow-lg flex flex-col items-center">
                <h2 class="text-2xl font-bold text-gray-800 mb-4 text-center">Signal Triangulation</h2>
                <button id="detectButton" onclick="startDetection()" class="w-full bg-secondary text-white py-3 rounded-xl font-bold text-lg hover:bg-emerald-600 focus:outline-none focus:ring-4 focus:ring-emerald-300 control-button transition duration-150 ease-in-out disabled:opacity-50" disabled>
                    Start Full 360° Scan
                </button>
                <p id="detectionStatus" class="text-sm mt-2 text-gray-600">Idle. Press 'Start Scan' to begin.</p>
                <div class="mt-4 w-full max-w-xs">
                    <canvas id="polarCanvas" width="300" height="300" class="bg-gray-100 rounded-full border border-gray-300"></canvas>
                </div>
                <div id="maxSignal" class="mt-4 text-lg font-semibold text-gray-700">Max Signal: N/A</div>
            </div>
        </div>
    </div>

    <script>
        const API_URL = '/live-port-5000/api';
        const canvas = document.getElementById('polarCanvas');
        const ctx = canvas.getContext('2d');
        const statusIndicators = {
            car: document.getElementById('carStatus'),
            sdr: document.getElementById('sdrStatus'),
            init: document.getElementById('initButton'),
            initMsg: document.getElementById('initMessage'),
            detectBtn: document.getElementById('detectButton'),
            detectionStatus: document.getElementById('detectionStatus'),
            maxSignal: document.getElementById('maxSignal')
        };
        let detectionInterval = null;
        let isDetectionRunning = false;

        // --- Utility Functions ---
        function updateStatus(carConnected, sdrReady, isRunning) {
            // Car Status
            statusIndicators.car.textContent = 'Car: ' + (carConnected ? 'Connected' : 'Disconnected');
            statusIndicators.car.className = carConnected ? 'flex items-center text-sm font-medium text-secondary' : 'flex items-center text-sm font-medium text-red-600';
            // SDR Status
            statusIndicators.sdr.textContent = 'SDR: ' + (sdrReady ? 'Ready' : 'Not Ready');
            statusIndicators.sdr.className = sdrReady ? 'flex items-center text-sm font-medium text-secondary' : 'flex items-center text-sm font-medium text-red-600';

            // Detection Button State
            if (carConnected && sdrReady) {
                statusIndicators.detectBtn.disabled = isRunning;
                statusIndicators.init.disabled = true;
            } else {
                statusIndicators.detectBtn.disabled = true;
                statusIndicators.init.disabled = false;
            }
            
            // Detection Running State
            isDetectionRunning = isRunning;
            if (isRunning) {
                statusIndicators.detectionStatus.textContent = "Scanning in progress... Do not interrupt.";
            } else {
                statusIndicators.detectionStatus.textContent = "Scan complete. Ready for new scan.";
            }
        }

        // --- Driver Initialization ---
        async function initDrivers() {
            statusIndicators.initMsg.textContent = "Connecting... Please wait.";
            statusIndicators.init.disabled = true;

            try {
                const response = await fetch(`${API_URL}/init_drivers`, { method: 'POST' });
                const data = await response.json();
                
                statusIndicators.initMsg.textContent = data.message;
                
                if (data.status === 'success') {
                    startPolling();
                }
            } catch (error) {
                console.error("Initialization error:", error);
                statusIndicators.initMsg.textContent = "Connection failed. Check server console.";
            } finally {
                // Re-enable in case of failure, polling handles success
                if (statusIndicators.initMsg.textContent.includes('failed')) {
                    statusIndicators.init.disabled = false;
                }
            }
        }
        
        // --- Manual Movement Control ---
        async function moveCar(direction) {
            try {
                const response = await fetch(`${API_URL}/move/${direction}`, { method: 'POST' });
                const data = await response.json();
                if (data.status === 'error') {
                     console.log('Movement Failed: ' + data.message);
                } else {
                    console.log(data.message);
                }
            } catch (error) {
                console.error("Move error:", error);
            }
        }
        
        // --- Detection Logic ---
        async function startDetection() {
            if (isDetectionRunning) return;

            // Clear previous results visually
            drawPolarPlot({});
            statusIndicators.maxSignal.textContent = "Max Signal: N/A";

            try {
                const response = await fetch(`${API_URL}/detect/start`, { method: 'POST' });
                const data = await response.json();
                if (data.status === 'error') {
                    alert('Detection Failed: ' + data.message);
                } else {
                    console.log(data.message);
                }
            } catch (error) {
                console.error("Start detection error:", error);
            }
        }
        
        // --- Polling and Plotting ---

        function drawPolarPlot(results) {
            const size = canvas.width;
            const center = size / 2;
            const radius = size * 0.45;
            
            ctx.clearRect(0, 0, size, size);
            
            // 1. Draw Grid (Aesthetic)
            ctx.strokeStyle = '#ccc';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(center, center, radius / 3, 0, 2 * Math.PI);
            ctx.arc(center, center, radius * 2 / 3, 0, 2 * Math.PI);
            ctx.arc(center, center, radius, 0, 2 * Math.PI);
            ctx.moveTo(center, center); ctx.lineTo(center, center - radius); // 90
            ctx.moveTo(center, center); ctx.lineTo(center, center + radius); // 270
            ctx.moveTo(center, center); ctx.lineTo(center + radius, center); // 0
            ctx.moveTo(center, center); ctx.lineTo(center - radius, center); // 180
            ctx.stroke();
            
            // 2. Data Plotting
            const dataPoints = Object.entries(results);
            if (dataPoints.length === 0) {
                 ctx.font = '16px sans-serif';
                 ctx.textAlign = 'center';
                 ctx.fillStyle = '#6b7280';
                 ctx.fillText('No Scan Data', center, center);
                 return;
            }

            // Normalization: Map dB values to a radius scale
            // Assume min_dB is -120 (noise floor) and max_dB is -40 (very strong)
            const MIN_DB = -120; 
            const MAX_DB = -40;
            const dbRange = MAX_DB - MIN_DB;
            
            let maxPower = -Infinity;
            let maxAngle = 0;

            ctx.lineWidth = 3;
            ctx.beginPath();
            
            dataPoints.forEach(([angleStr, power], index) => {
                const angleDeg = parseFloat(angleStr);
                const angleRad = (angleDeg - 90) * (Math.PI / 180); // Convert to radians, adjust for 0° being East/Right
                
                // Calculate normalized radius (0 to 1)
                let normalizedPower = Math.max(0, Math.min(1, (power - MIN_DB) / dbRange));
                
                // Scale to canvas radius
                const dataRadius = normalizedPower * radius; 
                
                // Convert polar to cartesian coordinates
                const x = center + dataRadius * Math.cos(angleRad);
                const y = center + dataRadius * Math.sin(angleRad);
                
                // Track max power
                if (power > maxPower) {
                    maxPower = power;
                    maxAngle = angleDeg;
                }
                
                // Draw line segment
                if (index === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
                
                // Draw a dot for the point
                ctx.fillStyle = normalizedPower > 0.8 ? '#dc2626' : '#10b981';
                ctx.beginPath();
                ctx.arc(x, y, 3, 0, 2 * Math.PI);
                ctx.fill();

                // Draw angle label for the point
                ctx.font = '10px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillStyle = '#1f2937';
                
                if (dataPoints.length < 20) { // Only label a few points to avoid clutter
                    const labelRadius = radius + 10;
                    const labelX = center + labelRadius * Math.cos(angleRad);
                    const labelY = center + labelRadius * Math.sin(angleRad);
                    ctx.fillText(`${angleDeg}°`, labelX, labelY);
                }
            });

            ctx.closePath();
            ctx.strokeStyle = '#4f46e5'; // Primary line color
            ctx.stroke();
            
            // Display max signal result
            statusIndicators.maxSignal.textContent = `Max Signal: ${maxPower.toFixed(2)} dB at ${maxAngle}°`;
        }

        async function pollStatus() {
            try {
                const response = await fetch(`${API_URL}/detect/status`);
                const data = await response.json();
                
                updateStatus(data.car_connected, data.sdr_ready, data.running);
                
                // Update plot with current results
                drawPolarPlot(data.results);

            } catch (error) {
                console.error("Polling error:", error);
                // Clear state on error to allow user to retry init
                updateStatus(false, false, false);
            }
        }

        function startPolling() {
            if (detectionInterval) {
                clearInterval(detectionInterval);
            }
            // Poll every 500ms for updates
            detectionInterval = setInterval(pollStatus, 500);
            pollStatus(); // Initial status check
        }
        
        // Start polling when the page loads
        document.addEventListener('DOMContentLoaded', startPolling);
    </script>
</body>
</html>
    """
    return render_template_string(html_content)

if __name__ == '__main__':
    # Start the Flask app
    # host='0.0.0.0' allows access from other devices on the network
    app.run(host='0.0.0.0', port=5000, debug=True)