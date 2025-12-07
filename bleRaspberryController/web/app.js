const API_URL = '/live-port-5000/api';
const LOGIN_KEY = 'ble_car_logged_in';

// --- Authentication ---
function checkLoginStatus() {
    const isLoggedIn = localStorage.getItem(LOGIN_KEY) === 'true';
    const loginOverlay = document.getElementById('loginOverlay');
    
    if (loginOverlay) {
        if (isLoggedIn) {
            loginOverlay.classList.add('hidden');
        } else {
            loginOverlay.classList.remove('hidden');
            // Focus password input
            setTimeout(() => {
                const passwordInput = document.getElementById('passwordInput');
                if (passwordInput) passwordInput.focus();
            }, 100);
        }
    }
    return isLoggedIn;
}

async function handleLogin(event) {
    event.preventDefault();
    
    const passwordInput = document.getElementById('passwordInput');
    const loginError = document.getElementById('loginError');
    const password = passwordInput.value;
    
    try {
        const response = await fetch(`${API_URL}/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ password })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            localStorage.setItem(LOGIN_KEY, 'true');
            loginError.classList.add('hidden');
            checkLoginStatus();
        } else {
            loginError.classList.remove('hidden');
            passwordInput.value = '';
            passwordInput.focus();
        }
    } catch (error) {
        console.error('Login error:', error);
        loginError.textContent = 'Connection error';
        loginError.classList.remove('hidden');
    }
    
    return false;
}

function logout() {
    localStorage.removeItem(LOGIN_KEY);
    checkLoginStatus();
}

// Check login on page load
checkLoginStatus();

const canvas = document.getElementById('polarCanvas');
const ctx = canvas.getContext('2d');
const statusIndicators = {
    car: document.getElementById('carStatus'),
    sdr: document.getElementById('sdrStatus'),
    init: document.getElementById('initButton'),
    initMsg: document.getElementById('initMessage'),
    detectBtn: document.getElementById('detectButton'),
    detectionStatus: document.getElementById('detectionStatus'),
    maxSignal: document.getElementById('maxSignal'),
    currentDb: document.getElementById('currentDb'),
    rotationDurationDisplay: document.getElementById('rotationDurationDisplay')
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

// --- Speed Control ---
async function setSpeed(value) {
    try {
        const response = await fetch(`${API_URL}/speed/${value}`, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            document.getElementById('speedValue').textContent = data.speed;
            console.log(`Speed set to ${data.speed}%`);
        } else {
            console.log('Speed Failed: ' + data.message);
        }
    } catch (error) {
        console.error("Speed error:", error);
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

// --- Stop Detection ---
async function stopDetection() {
    try {
        const response = await fetch(`${API_URL}/detect/stop`, { method: 'POST' });
        const data = await response.json();
        
        // Clear the plot and table
        drawPolarPlot({});
        statusIndicators.maxSignal.textContent = "Max Signal: N/A";
        statusIndicators.detectionStatus.textContent = data.message;
        
        console.log(data.message);
    } catch (error) {
        console.error("Stop detection error:", error);
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
    
    // Update the signal table
    updateSignalTable(results);
    
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
    
    // Store point positions for drawing dots later
    const pointPositions = [];
    
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
        
        // Store position for later dot drawing
        pointPositions.push({ x, y, angleDeg, angleRad, normalizedPower });
    });
    
    // Draw all dots (separate from any path building)
    pointPositions.forEach(({ x, y, angleDeg, angleRad, normalizedPower }) => {
        // Draw a dot for the point - red for strong signal, green for weak
        ctx.fillStyle = normalizedPower > 0.8 ? '#dc2626' : '#10b981';
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, 2 * Math.PI);
        ctx.fill();

        // Draw angle label for the point
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = '#1f2937';
        
        if (dataPoints.length < 20) { // Only label a few points to avoid clutter
            const labelRadius = radius + 15;
            const labelX = center + labelRadius * Math.cos(angleRad);
            const labelY = center + labelRadius * Math.sin(angleRad);
            ctx.fillText(`${angleDeg}°`, labelX, labelY);
        }
    });
    
    // Display max signal result
    statusIndicators.maxSignal.textContent = `Max Signal: ${maxPower.toFixed(2)} dB at ${maxAngle}°`;
}

// --- Signal Table Update ---
function updateSignalTable(results) {
    const tableBody = document.getElementById('signalTableBody');
    if (!tableBody) return;
    
    const dataPoints = Object.entries(results);
    
    if (dataPoints.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="2" class="py-3 text-center text-gray-400">No scan data</td></tr>';
        return;
    }
    
    // Sort by angle
    dataPoints.sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]));
    
    // Find max power for highlighting
    const maxPower = Math.max(...dataPoints.map(([_, power]) => power));
    
    let html = '';
    dataPoints.forEach(([angle, power]) => {
        const isMax = power === maxPower;
        const rowClass = isMax ? 'bg-green-100 font-semibold' : (parseInt(angle) % 90 === 0 ? 'bg-gray-50' : '');
        const powerClass = isMax ? 'text-green-600' : (power > -60 ? 'text-red-500' : 'text-gray-700');
        
        html += `<tr class="${rowClass}">
            <td class="py-1.5 px-3 border-b border-gray-100">${angle}°</td>
            <td class="py-1.5 px-3 border-b border-gray-100 text-right ${powerClass}">${power.toFixed(2)} dB</td>
        </tr>`;
    });
    
    tableBody.innerHTML = html;
}

async function pollStatus() {
    try {
        const response = await fetch(`${API_URL}/detect/status`);
        const data = await response.json();
        
        updateStatus(data.car_connected, data.sdr_ready, data.running);
        
        // Update current dB display
        if (data.current_db !== null) {
            statusIndicators.currentDb.textContent = `${data.current_db} dB`;
            // Color based on signal strength
            if (data.current_db > -60) {
                statusIndicators.currentDb.className = 'text-2xl font-bold text-red-400 ml-2';
            } else if (data.current_db > -90) {
                statusIndicators.currentDb.className = 'text-2xl font-bold text-yellow-400 ml-2';
            } else {
                statusIndicators.currentDb.className = 'text-2xl font-bold text-green-400 ml-2';
            }
        } else {
            statusIndicators.currentDb.textContent = '-- dB';
            statusIndicators.currentDb.className = 'text-2xl font-bold text-gray-500 ml-2';
        }
        
        // Update plot with current results
        drawPolarPlot(data.results);
        
        // Update rotation duration display
        if (data.rotation_duration !== undefined && statusIndicators.rotationDurationDisplay) {
            statusIndicators.rotationDurationDisplay.textContent = `${data.rotation_duration}s`;
        }

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

// --- Calibration ---
async function calibrateRotation(duration) {
    const calibrateBtn = document.getElementById('calibrateButton');
    const calibrateMsg = document.getElementById('calibrateMessage');
    
    calibrateBtn.disabled = true;
    calibrateMsg.textContent = `Rotating for ${duration} seconds...`;
    calibrateMsg.className = 'text-sm mt-2 text-blue-600';
    
    try {
        const response = await fetch(`${API_URL}/calibrate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ duration: parseFloat(duration) })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            calibrateMsg.textContent = data.message + (data.saved ? ' (Saved to config)' : '');
            calibrateMsg.className = 'text-sm mt-2 text-green-600';
            // Update rotation duration display immediately
            if (statusIndicators.rotationDurationDisplay) {
                statusIndicators.rotationDurationDisplay.textContent = `${duration}s`;
            }
        } else {
            calibrateMsg.textContent = 'Error: ' + data.message;
            calibrateMsg.className = 'text-sm mt-2 text-red-600';
        }
    } catch (error) {
        console.error("Calibration error:", error);
        calibrateMsg.textContent = 'Calibration failed. Check server console.';
        calibrateMsg.className = 'text-sm mt-2 text-red-600';
    } finally {
        calibrateBtn.disabled = false;
    }
}

// --- Load Saved Config ---
async function loadSavedConfig() {
    try {
        const response = await fetch(`${API_URL}/config`);
        const data = await response.json();
        
        if (data.status === 'success' && data.config) {
            // Apply saved speed
            if (data.config.speed !== undefined) {
                const speedSlider = document.getElementById('speedSlider');
                const speedValue = document.getElementById('speedValue');
                if (speedSlider && speedValue) {
                    speedSlider.value = data.config.speed;
                    speedValue.textContent = data.config.speed;
                }
            }
            
            // Apply saved rotation duration
            if (data.config.rotation_duration !== undefined) {
                const rotationInput = document.getElementById('rotationDuration');
                if (rotationInput) {
                    rotationInput.value = data.config.rotation_duration;
                }
                if (statusIndicators.rotationDurationDisplay) {
                    statusIndicators.rotationDurationDisplay.textContent = `${data.config.rotation_duration}s`;
                }
            }
            
            // Apply saved measurement time
            if (data.config.measurement_time !== undefined) {
                const measurementInput = document.getElementById('measurementTime');
                if (measurementInput) {
                    measurementInput.value = data.config.measurement_time;
                }
            }
            
            console.log('Loaded saved config:', data.config);
        }
    } catch (error) {
        console.error('Error loading saved config:', error);
    }
}

// --- Save Settings ---
async function saveSettings() {
    const calibrateMsg = document.getElementById('calibrateMessage');
    const rotationDuration = document.getElementById('rotationDuration').value;
    const measurementTime = document.getElementById('measurementTime').value;
    
    calibrateMsg.textContent = 'Saving settings...';
    calibrateMsg.className = 'text-sm mt-2 text-blue-600';
    
    try {
        const response = await fetch(`${API_URL}/settings`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                rotation_duration: parseFloat(rotationDuration),
                measurement_time: parseFloat(measurementTime)
            })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            calibrateMsg.textContent = 'Settings saved successfully!';
            calibrateMsg.className = 'text-sm mt-2 text-green-600';
            // Update rotation duration display
            if (statusIndicators.rotationDurationDisplay) {
                statusIndicators.rotationDurationDisplay.textContent = `${rotationDuration}s`;
            }
        } else {
            calibrateMsg.textContent = 'Error: ' + data.message;
            calibrateMsg.className = 'text-sm mt-2 text-red-600';
        }
    } catch (error) {
        console.error("Save settings error:", error);
        calibrateMsg.textContent = 'Failed to save settings. Check server console.';
        calibrateMsg.className = 'text-sm mt-2 text-red-600';
    }
}

// --- Test Rotation ---
async function testRotation() {
    const calibrateBtn = document.getElementById('calibrateButton');
    const calibrateMsg = document.getElementById('calibrateMessage');
    const rotationDuration = document.getElementById('rotationDuration').value;
    
    calibrateBtn.disabled = true;
    calibrateMsg.textContent = `Testing rotation for ${rotationDuration} seconds...`;
    calibrateMsg.className = 'text-sm mt-2 text-blue-600';
    
    try {
        const response = await fetch(`${API_URL}/calibrate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ duration: parseFloat(rotationDuration), save: false })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            calibrateMsg.textContent = 'Rotation test completed!';
            calibrateMsg.className = 'text-sm mt-2 text-green-600';
        } else {
            calibrateMsg.textContent = 'Error: ' + data.message;
            calibrateMsg.className = 'text-sm mt-2 text-red-600';
        }
    } catch (error) {
        console.error("Test rotation error:", error);
        calibrateMsg.textContent = 'Rotation test failed. Check server console.';
        calibrateMsg.className = 'text-sm mt-2 text-red-600';
    } finally {
        calibrateBtn.disabled = false;
    }
}

// Start polling when the page loads
document.addEventListener('DOMContentLoaded', () => {
    startPolling();
    loadSavedConfig();  // Load saved config values
});

