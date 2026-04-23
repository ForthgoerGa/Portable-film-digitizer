"""
Low-level motor control and scanning APIs.

This module provides the core hardware abstraction layer for stepper motors
and basic scanning operations.
"""

import time
from typing import Callable

import RPi.GPIO as GPIO

try:
    from .config import (
        STEP_X,
        STEP_Y,
        DIR_X,
        DIR_Y,
        RPM,
        SPR,
        STEP_DELAY,
        RAMP_START,
        RAMP_FACTOR,
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
    )
except ImportError:
    from config import (
        STEP_X,
        STEP_Y,
        DIR_X,
        DIR_Y,
        RPM,
        SPR,
        STEP_DELAY,
        RAMP_START,
        RAMP_FACTOR,
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
    )


class StepperMotor:
    """
    Represents a single stepper motor with GPIO control.

    Handles direction setting, stepping, and acceleration ramps.
    """

    def __init__(self, step_pin: int, dir_pin: int, invert_dir: bool = False):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.invert_dir = invert_dir
        self.direction = 1  # 1 = forward, 0 = backward
        self.position = 0  # Track current position in steps from origin

        # Setup GPIO
        GPIO.setup(step_pin, GPIO.OUT)
        GPIO.setup(dir_pin, GPIO.OUT)
        GPIO.output(step_pin, GPIO.LOW)

    def set_direction(self, direction: int) -> None:
        """Set motor direction (0=backward, 1=forward)."""
        self.direction = direction
        value = GPIO.HIGH if direction else GPIO.LOW

        if self.invert_dir:
            value = GPIO.LOW if value == GPIO.HIGH else GPIO.HIGH

        GPIO.output(self.dir_pin, value)

    def step(self, delay: float) -> None:
        """Perform a single step with the given delay."""
        GPIO.output(self.step_pin, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(self.step_pin, GPIO.LOW)
        time.sleep(delay)

    def ramp_profile(self, steps: int, target_delay: float) -> list[float]:
        """
        Generate acceleration/deceleration ramp delays.

        Returns list of delays for smooth acceleration to target_delay.
        """
        ramp = []
        delay = RAMP_START

        while delay > target_delay:
            ramp.append(delay)
            delay *= RAMP_FACTOR

        ramp_len = min(len(ramp), steps // 2)
        return ramp[:ramp_len]

    def move_steps(self, steps: int, target_delay: float) -> None:
        """
        Move the motor by the specified number of steps.

        Uses acceleration ramping for smooth movement.
        """
        ramp = self.ramp_profile(steps, target_delay)
        ramp_len = len(ramp)

        # Accelerate
        for d in ramp:
            self.step(d)

        # Constant speed
        for _ in range(steps - 2 * ramp_len):
            self.step(target_delay)

        # Decelerate
        for d in reversed(ramp):
            self.step(d)

        # Update position: positive steps move forward, negative move backward
        self.position += steps

    def get_position(self) -> int:
        """Return current position in steps from origin."""
        return self.position


class Scanner:
    """
    High-level scanner interface for film digitization.

    Provides non-blocking motor movement APIs and image capture coordination.
    """

    def __init__(self):
        # Initialize GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Create motors
        self.motor_x = StepperMotor(STEP_X, DIR_X, invert_dir=True)
        self.motor_y = StepperMotor(STEP_Y, DIR_Y)

        # Capture callback (can be overridden for testing)
        self.capture_callback: Callable[[int, int], None] = lambda row, col: None

    def set_capture_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set the callback function for image capture."""
        self.capture_callback = callback

    def move_x(self, steps: int) -> None:
        """Move X-axis motor by specified number of steps."""
        direction = 1 if steps >= 0 else 0
        steps = abs(steps)

        self.motor_x.set_direction(direction)
        self.motor_x.move_steps(steps, STEP_DELAY)

    def move_y(self, steps: int) -> None:
        """Move Y-axis motor by specified number of steps."""
        direction = 1 if steps >= 0 else 0
        steps = abs(steps)

        self.motor_y.set_direction(direction)
        self.motor_y.move_steps(steps, STEP_DELAY)

    def home_x(self) -> None:
        """Return X-axis to origin by moving to position 0."""
        current = self.motor_x.position
        if current != 0:
            self.move_x(-current)

    def home_y(self) -> None:
        """Return Y-axis to origin by moving to position 0."""
        current = self.motor_y.position
        if current != 0:
            self.move_y(-current)

    def return_to_origin(self) -> None:
        """Return both axes to origin position."""
        self.home_x()
        self.home_y()

    def set_home(self) -> None:
        """Set current position as home (origin = 0,0)."""
        self.motor_x.position = 0
        self.motor_y.position = 0

    def get_position(self) -> tuple[int, int]:
        """Return current (x, y) position."""
        return (self.motor_x.position, self.motor_y.position)

    def capture(self, row: int, col: int) -> None:
        """Capture an image at the specified grid position."""
        self.capture_callback(row, col)

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        GPIO.cleanup()
