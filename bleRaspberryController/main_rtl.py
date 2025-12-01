from rtl_sdr_driver import RtlSdrDriver
import time
import numpy as np

if __name__ == "__main__":
    # Example parameters based on your request
    WATCH_FREQ_MHZ = 433.4
    SAMPLE_RATE_HZ = 1024000

    driver = RtlSdrDriver(WATCH_FREQ_MHZ, SAMPLE_RATE_HZ, 0)
    
    print(f"Watching {WATCH_FREQ_MHZ} MHz... Press Ctrl+C to stop.")
    
    # Storage for averaging
    readings = []
    last_print_time = time.time()
    
    try:
        while True:
            # Get the power of the signal
            power = driver.watch()
            readings.append(power)
            
            # Check if 1 second has passed
            current_time = time.time()
            if current_time - last_print_time >= 1.0:
                if readings:
                    avg_power = np.mean(readings)
                    
                    # Simple visualization
                    bar_length = int(avg_power + 100) // 2 
                    bar = "#" * max(0, bar_length)
                    
                    print(f"Average Power (1s): {avg_power:.2f} dB  [{bar}]")
                
                # Reset buffer and timer
                readings = []
                last_print_time = current_time
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        driver.close()
