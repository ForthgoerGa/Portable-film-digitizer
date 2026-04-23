"""
Low-level motor control and scanning APIs.

This module provides the core hardware abstraction layer for stepper motors
and basic scanning operations.
"""

import time
from typing import Callable
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
import threading

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
        CAPTURES_DIR,
    )
    from .picamera2 import Picamera2
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
        CAPTURES_DIR,
    )
    from picamera2 import Picamera2


class MotorMode(Enum):
    IDLE = "idle"
    MOVING = "moving"
    RESET = "reset"


@dataclass
class MotorState:
    position: int = 0
    mode: MotorMode = MotorMode.RESET
    cv: threading.Condition = field(default_factory=threading.Condition)

    def start_move(self) -> None:
        with self.cv:
            if self.mode == MotorMode.MOVING:
                raise RuntimeError("Motor is moving")
            self.mode = MotorMode.MOVING

    def start_home(self) -> int:
        with self.cv:
            while self.mode == MotorMode.MOVING:
                self.cv.wait()
            if self.position == 0:
                self.mode = MotorMode.RESET
                self.cv.notify_all()
                return 0
            self.mode = MotorMode.MOVING
            return -self.position

    def finish_move(self, delta: int) -> None:
        with self.cv:
            self.position += delta
            self.mode = MotorMode.RESET if self.position == 0 else MotorMode.IDLE
            self.cv.notify_all()

    def set_home(self) -> None:
        with self.cv:
            if self.mode == MotorMode.MOVING:
                raise RuntimeError("Motor is moving")
            self.position = 0
            self.mode = MotorMode.RESET
            self.cv.notify_all()


class StepperMotor:
    """
    Represents a single stepper motor with GPIO control.

    Handles direction setting, stepping, and acceleration ramps.
    """

    def __init__(self, step_pin: int, dir_pin: int, invert_dir: bool = False):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.invert_dir = invert_dir
        self.state = MotorState()

        # Setup GPIO
        GPIO.setup(step_pin, GPIO.OUT)
        GPIO.setup(dir_pin, GPIO.OUT)
        GPIO.output(step_pin, GPIO.LOW)

    def set_direction(self, direction: int) -> None:
        """Set motor direction (0=backward, 1=forward)."""
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

    def move_steps(self, delta: int, target_delay: float) -> None:
        """
        Move the motor by the specified number of steps.

        Uses acceleration ramping for smooth movement.
        """
        if delta == 0:
            return
        steps = abs(delta)
        direction = 1 if delta > 0 else 0
        self.set_direction(direction)

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

    def move(self, delta: int, target_delay: float) -> None:
        """Move the motor by the signed delta, updating state."""
        if delta == 0:
            return
        self.state.start_move()
        try:
            self.move_steps(delta, target_delay)
        finally:
            self.state.finish_move(delta)

    def home(self, target_delay: float) -> None:
        """Return the motor to home position."""
        delta = self.state.start_home()
        if delta == 0:
            return
        try:
            self.move_steps(delta, target_delay)
        finally:
            self.state.finish_move(delta)

    def set_home(self) -> None:
        """Set current position as home."""
        self.state.set_home()

    def get_state(self) -> MotorState:
        """Get current motor state."""
        return self.state


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

        # Initialize camera
        try:
            self.camera = Picamera2()
            config = self.camera.create_still_configuration()
            self.camera.configure(config)
            self.camera.start()
            self.camera_available = True
        except IndexError:
            self.camera = None
            self.camera_available = False

    def set_capture_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set the callback function for image capture."""
        self.capture_callback = callback

    def move_x(self, steps: int) -> None:
        """Move X-axis motor by specified number of steps."""
        self.motor_x.move(steps, STEP_DELAY)

    def move_y(self, steps: int) -> None:
        """Move Y-axis motor by specified number of steps."""
        self.motor_y.move(steps, STEP_DELAY)

    def return_to_origin(self) -> None:
        """Return both axes to origin position."""
        self.motor_x.home(STEP_DELAY)
        self.motor_y.home(STEP_DELAY)

    def set_home(self) -> None:
        """Set current position as home (origin = 0,0)."""
        self.motor_x.set_home()
        self.motor_y.set_home()

    def get_position(self) -> tuple[int, int]:
        """Return current (x, y) position."""
        return (self.motor_x.state.position, self.motor_y.state.position)

    def get_motor_states(self) -> tuple[MotorState, MotorState]:
        """Return current motor states."""
        return (self.motor_x.get_state(), self.motor_y.get_state())

    def is_any_motor_moving(self) -> bool:
        """Check if any motor is currently moving."""
        return (
            self.motor_x.state.mode == MotorMode.MOVING
            or self.motor_y.state.mode == MotorMode.MOVING
        )

    def capture(self, row: int, col: int) -> None:
        """Capture an image at the specified grid position."""
        if not self.camera_available:
            print("Camera not available, skipping capture")
            return
        filename = CAPTURES_DIR / f"row_{row}_col_{col}.jpg"
        self.camera.capture_file(str(filename))
        print(f"Captured {filename}")

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        if self.camera_available:
            self.camera.close()
        GPIO.cleanup()
