"""
Configuration constants for the film digitizer scanner.

This module centralizes all hardware and scanning parameters.
"""

# GPIO pin assignments
STEP_X = 21
STEP_Y = 20
DIR_X = 26
DIR_Y = 19

# Motor configuration
RPM = 200  # realistic speed
SPR = 200 * 16  # steps per revolution (microstepping)

# Timing configuration
STEP_DELAY = 0.00001  # ~2 kHz stepping (stable in Python)

# Ramp profile for smooth acceleration/deceleration
RAMP_START = 0.002
RAMP_FACTOR = 0.90

# Scan parameters (35mm film)
X_SEGMENTS = 4
Y_SEGMENTS = 2
X_STEPS_PER_SEG = 20000
Y_STEPS_PER_SEG = 8000
