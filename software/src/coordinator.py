"""
Scanner state machine and scan lifecycle coordinator.

This module manages the overall scanning process with proper state tracking,
progress monitoring, and cancellation support.
"""

import time
import threading
from enum import Enum
from typing import Dict, Any, Optional

try:
    from .scanner import Scanner
    from .config import X_SEGMENTS, Y_SEGMENTS, X_STEPS_PER_SEG, Y_STEPS_PER_SEG
except ImportError:
    from scanner import Scanner
    from config import X_SEGMENTS, Y_SEGMENTS, X_STEPS_PER_SEG, Y_STEPS_PER_SEG


class ScannerState(Enum):
    """Enumeration of possible scanner states."""

    IDLE = "idle"
    SCANNING = "scanning"
    RETURNING_HOME = "returning_home"
    CANCELLED = "cancelled"
    ERROR = "error"


class ScannerCoordinator:
    """
    Coordinates the film scanning process with state machine management.

    Handles scan lifecycle, progress tracking, and user control operations.
    """

    def __init__(self):
        self.scanner = Scanner()
        self.state = ScannerState.IDLE
        self.current_row = 0
        self.current_col = 0
        self.cancel_flag = False
        self.error_message = ""
        self._lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None

    def start_scan(self, film_format: str = "35mm") -> None:
        """
        Start a new scanning operation.

        Args:
            film_format: Film format (e.g., "35mm", "120", "4x5")

        Raises:
            RuntimeError: If a scan is already in progress
        """
        with self._lock:
            if self.state != ScannerState.IDLE:
                raise RuntimeError("Scan already in progress")

            self.state = ScannerState.SCANNING
            self.current_row = 0
            self.current_col = 0
            self.cancel_flag = False
            self.error_message = ""

            self._scan_thread = threading.Thread(
                target=self._run_scan, args=(film_format,), daemon=True
            )
            self._scan_thread.start()

    def cancel_scan(self) -> None:
        """Cancel the current scanning operation."""
        with self._lock:
            if self.state == ScannerState.SCANNING:
                self.cancel_flag = True
                self.state = ScannerState.CANCELLED

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current scanner status and progress.

        Returns:
            Dictionary containing state, progress, and any error information
        """
        with self._lock:
            status = {
                "state": self.state.value,
                "current_row": self.current_row,
                "current_col": self.current_col,
                "total_rows": Y_SEGMENTS,
                "total_cols": X_SEGMENTS,
                "progress": self._calculate_progress(),
            }

            if self.state == ScannerState.ERROR:
                status["error"] = self.error_message

            return status

    def _calculate_progress(self) -> float:
        """Calculate scan completion percentage."""
        if self.state not in [ScannerState.SCANNING, ScannerState.RETURNING_HOME]:
            return 0.0

        total_positions = X_SEGMENTS * Y_SEGMENTS
        completed_positions = self.current_row * X_SEGMENTS + self.current_col

        if self.state == ScannerState.RETURNING_HOME:
            # Add extra progress for return-to-home phase
            return min(100.0, (completed_positions + 1) / total_positions * 100.0)
        else:
            return completed_positions / total_positions * 100.0

    def _run_scan(self, film_format: str) -> None:
        """
        Execute the scanning operation in a background thread.

        Performs serpentine scanning pattern across the film.
        """
        try:
            # Start moving right
            direction = 1
            self.scanner.motor_x.set_direction(direction)

            for row in range(Y_SEGMENTS):
                with self._lock:
                    self.current_row = row

                for col in range(X_SEGMENTS):
                    with self._lock:
                        self.current_col = col

                    # Check for cancellation
                    with self._lock:
                        if self.cancel_flag:
                            return

                    # Capture at each grid point
                    self.scanner.capture(row, col)

                    # Move X except at end of row
                    if col < X_SEGMENTS - 1:
                        steps = X_STEPS_PER_SEG if direction else -X_STEPS_PER_SEG
                        self.scanner.move_x(steps)

                # Move Y down except last row
                if row < Y_SEGMENTS - 1:
                    self.scanner.move_y(Y_STEPS_PER_SEG)

                # Reverse X direction for serpentine pattern
                direction ^= 1
                self.scanner.motor_x.set_direction(direction)

            # Return to origin
            with self._lock:
                self.state = ScannerState.RETURNING_HOME

            self.scanner.return_to_origin()

            # Complete
            with self._lock:
                self.state = ScannerState.IDLE
                self.current_row = 0
                self.current_col = 0

        except Exception as e:
            with self._lock:
                self.state = ScannerState.ERROR
                self.error_message = str(e)

    def cleanup(self) -> None:
        """Clean up resources."""
        self.scanner.cleanup()
