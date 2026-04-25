"""
Low-level motor control and scanning APIs.

This module provides the core hardware abstraction layer for stepper motors
and basic scanning operations.
"""

import time
from typing import Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import threading
from pathlib import Path

import RPi.GPIO as GPIO

try:
    from libcamera import controls
except ImportError:
    controls = None

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
        RAW_CAPTURE_ENABLED,
        CAPTURE_EXTENSION,
        PREVIEW_EXTENSION,
        CAPTURE_EXPOSURE_US,
        CAPTURE_ANALOGUE_GAIN,
        CAPTURE_COLOUR_GAINS,
        CAPTURE_AWB_ENABLE,
        CAPTURE_DENOISE_MODE,
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
        RAW_CAPTURE_ENABLED,
        CAPTURE_EXTENSION,
        PREVIEW_EXTENSION,
        CAPTURE_EXPOSURE_US,
        CAPTURE_ANALOGUE_GAIN,
        CAPTURE_COLOUR_GAINS,
        CAPTURE_AWB_ENABLE,
        CAPTURE_DENOISE_MODE,
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
        self.motor_y = StepperMotor(STEP_Y, DIR_Y, invert_dir=True)

        # Initialize camera
        try:
            self.camera = Picamera2()
            config = self.camera.create_still_configuration(
                main={"format": "RGB888"},
                raw={},
                controls=self._camera_controls(),
            )
            self.camera.configure(config)
            self.camera.start()
            self.camera.set_controls(self._camera_controls())
            self.camera_available = True
        except IndexError:
            self.camera = None
            self.camera_available = False

    def _camera_controls(self) -> dict[str, Any]:
        """Fixed capture controls for RAW-friendly post-processing."""
        camera_controls: dict[str, Any] = {
            "AeEnable": False,
            "AwbEnable": bool(CAPTURE_AWB_ENABLE),
            "ExposureTime": int(CAPTURE_EXPOSURE_US),
            "AnalogueGain": float(CAPTURE_ANALOGUE_GAIN),
            "ColourGains": tuple(CAPTURE_COLOUR_GAINS),
        }
        denoise_mode = _noise_reduction_mode(CAPTURE_DENOISE_MODE)
        if denoise_mode is not None:
            camera_controls["NoiseReductionMode"] = denoise_mode
        return camera_controls

    def set_capture_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set the callback function for image capture."""
        self.capture_callback = callback

    def move_x(self, steps: int) -> None:
        """Move X-axis motor by specified number of steps."""
        _validate_move_delta(steps, X_STEPS_PER_SEG, "x")
        self.motor_x.move(steps, STEP_DELAY)

    def move_y(self, steps: int) -> None:
        """Move Y-axis motor by specified number of steps."""
        _validate_move_delta(steps, Y_STEPS_PER_SEG, "y")
        self.motor_y.move(steps, STEP_DELAY)

    def move_to(self, x_position: int, y_position: int) -> None:
        """Move to an absolute position using safe per-segment chunks."""
        current_x, current_y = self.get_position()
        self._move_axis_to(self.motor_x, int(x_position) - current_x, X_STEPS_PER_SEG)
        self._move_axis_to(self.motor_y, int(y_position) - current_y, Y_STEPS_PER_SEG)

    def return_to_origin(self) -> None:
        """Return both axes to origin position."""
        self._home_axis_segmented(self.motor_x, X_STEPS_PER_SEG)
        self._home_axis_segmented(self.motor_y, Y_STEPS_PER_SEG)

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
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"row_{row}_col_{col}"
        raw_filename = CAPTURES_DIR / f"{stem}{CAPTURE_EXTENSION}"
        preview_filename = CAPTURES_DIR / f"{stem}{PREVIEW_EXTENSION}"
        self.capture_to_files(raw_filename, preview_filename)
        print(f"Captured {raw_filename} and {preview_filename}")

    def capture_to_files(self, raw_filename: Path, preview_filename: Path) -> None:
        """Capture one fixed-control RAW DNG plus one browser preview JPEG."""
        if not self.camera_available:
            raise RuntimeError("Camera not available")
        raw_filename.parent.mkdir(parents=True, exist_ok=True)
        preview_filename.parent.mkdir(parents=True, exist_ok=True)
        request = self.camera.capture_request()
        try:
            if RAW_CAPTURE_ENABLED:
                self._save_dng(request, raw_filename)
            request.save("main", str(preview_filename))
        finally:
            request.release()

    def capture_preview(self, preview_filename: Path) -> None:
        """Capture one display preview frame without writing RAW data."""
        if not self.camera_available:
            raise RuntimeError("Camera not available")
        preview_filename.parent.mkdir(parents=True, exist_ok=True)
        request = self.camera.capture_request()
        try:
            request.save("main", str(preview_filename))
        finally:
            request.release()

    def _save_dng(self, request: Any, raw_filename: Path) -> None:
        """Save DNG across Picamera2/pidng versions.

        Some Pi images ship a Picamera2 helper that calls pidng with a
        ``file=`` keyword, while the installed pidng expects ``filename`` and
        returns bytes when no filename is provided. Keep the normal API path
        first, then fall back to the same raw buffer with a direct byte write.
        """
        try:
            request.save_dng(str(raw_filename), name="raw")
            return
        except TypeError as exc:
            if "unexpected keyword argument 'file'" not in str(exc):
                raise
            if raw_filename.exists() and raw_filename.stat().st_size == 0:
                raw_filename.unlink()

        import numpy as np
        from picamera2 import request as picam_request

        stream_name = "raw"
        if request.stream_map.get(stream_name) is None:
            raise RuntimeError(f"Stream {stream_name!r} is not defined")

        config = request.config[stream_name].copy()
        with picam_request._MappedBuffer(request, stream_name, write=False) as mapped:
            buffer = np.array(mapped, copy=False, dtype=np.uint8)
            raw = request.picam2.helpers._make_array_shared(buffer, config)

        fmt = picam_request.SensorFormat(config["format"])
        if fmt.packing == "PISP_COMP1":
            raw = request.picam2.helpers.decompress(raw)
            fmt.bit_depth = 16
            config["format"] = fmt.unpacked
            config["stride"] = raw.shape[1]
            config["framesize"] = raw.shape[0] * raw.shape[1]

        model = request.picam2.camera_properties.get("Model") or "Picamera2"
        camera = picam_request.Picamera2Camera(config, request.get_metadata(), model)
        writer = picam_request.PICAM2DNG(camera)
        writer.options(compress=request.picam2.options.get("compress_level", 0))
        dng_bytes = writer.convert(raw)
        raw_filename.write_bytes(dng_bytes)

    def _home_axis_segmented(self, motor: StepperMotor, max_delta: int) -> None:
        """Home an axis using chunks no larger than the configured segment size."""
        while motor.state.position != 0:
            remaining = -motor.state.position
            if remaining > 0:
                delta = min(remaining, max_delta)
            else:
                delta = max(remaining, -max_delta)
            motor.move(delta, STEP_DELAY)

    def _move_axis_to(self, motor: StepperMotor, delta: int, max_delta: int) -> None:
        """Move an axis by delta using chunks no larger than max_delta."""
        remaining = int(delta)
        while remaining != 0:
            if remaining > 0:
                step = min(remaining, max_delta)
            else:
                step = max(remaining, -max_delta)
            motor.move(step, STEP_DELAY)
            remaining -= step

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        if self.camera_available:
            self.camera.close()
        GPIO.cleanup()


def _validate_move_delta(delta: int, max_abs_delta: int, axis: str) -> None:
    if abs(int(delta)) > int(max_abs_delta):
        raise ValueError(
            f"{axis.upper()} move {delta} exceeds configured safe limit {max_abs_delta}"
        )


def _noise_reduction_mode(name: str):
    if controls is None or not hasattr(controls, "draft"):
        return None
    enum = getattr(controls.draft, "NoiseReductionModeEnum", None)
    if enum is None:
        return None
    return getattr(enum, str(name), None)
