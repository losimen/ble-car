import numpy as np
from rtlsdr import RtlSdr
import sys

class RtlSdrDriver:
    """
    A driver class to interface with an RTL-SDR dongle to measure signal power.
    """
    def __init__(self, watch_frequency_mhz, sample_rate=1024000, gain='auto'):
        """
        Initialize the RTL-SDR device.

        Args:
            watch_frequency_mhz (float): Center frequency to watch in MHz (e.g., 433.4).
            sample_rate (int): Sample rate in Hz (default 1024000).
            gain (str or float): Gain setting, 'auto' is recommended (default 'auto').
        """
        self.frequency_mhz = watch_frequency_mhz
        self.sample_rate = sample_rate
        self.sdr = None

        try:
            self.sdr = RtlSdr()
            
            # Configure the SDR
            self.sdr.sample_rate = self.sample_rate
            self.sdr.center_freq = self.frequency_mhz * 1e6  # Convert MHz to Hz
            self.sdr.gain = gain
            
            # Read a few samples to flush buffers and let gain settle
            self.sdr.read_samples(256)
            
            print(f"RtlSdrDriver initialized.")
            print(f" - Frequency: {self.frequency_mhz} MHz")
            print(f" - Sample Rate: {self.sample_rate} Hz")
            print(f" - Gain: {self.sdr.gain}")
            
        except Exception as e:
            print(f"Error initializing RTL-SDR: {e}")
            print("Ensure the device is plugged in and drivers are installed.")
            if self.sdr:
                self.sdr.close()
            sys.exit(1)

    def watch(self, num_samples=4096):
        """
        Reads a chunk of samples from the SDR and calculates the relative signal power.

        Args:
            num_samples (int): Number of samples to read for the measurement (power of 2 recommended).

        Returns:
            float: Relative signal power in Decibels (dB).
        """
        if not self.sdr:
            raise RuntimeError("SDR device not initialized.")

        try:
            # Read complex IQ samples
            samples = self.sdr.read_samples(num_samples)
            
            # Calculate power: Mean of the magnitude squared of the complex samples
            # We use numpy for efficient calculation
            # P = |I + jQ|^2
            power_linear = np.mean(np.abs(samples)**2)
            
            # Convert to Decibels (dB)
            # Add a small epsilon to avoid log(0)
            power_db = 10 * np.log10(power_linear + 1e-20)
            
            return power_db

        except Exception as e:
            print(f"Error reading samples: {e}")
            return -100.0 # Return noise floor value on error

    def close(self):
        """
        Closes the connection to the SDR device.
        """
        if self.sdr:
            self.sdr.close()
            self.sdr = None
            print("RTL-SDR device closed.")

    def __del__(self):
        self.close()
