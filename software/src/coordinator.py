"""
Scanner state machine and scan lifecycle coordinator.

This module manages the overall scanning process with proper state tracking,
progress monitoring, and cancellation support.
"""

import time
import threading
import shutil
import mimetypes
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional

try:
    import requests as _requests
except ImportError:  # pragma: no cover - Pi runtime should provide requests
    _requests = None

try:
    from .scanner import Scanner
    from .integrator import integrate_captures
    from .config import (
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
        CAPTURES_DIR,
    )
except ImportError:
    from scanner import Scanner
    from integrator import integrate_captures
    from config import (
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
        CAPTURES_DIR,
    )


_UPLOAD_TIMEOUT_S = 180
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_UPLOAD_LOG_INTERVAL = 16 * 1024 * 1024


class ScannerState(Enum):
    """Enumeration of possible scanner states."""

    IDLE = "idle"
    SCANNING = "scanning"
    STITCHING = "stitching"
    UPLOADING = "uploading"
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
        self.scan_order_index = 0
        self.cancel_flag = False
        self.error_message = ""
        self.integration_result: dict[str, Any] | None = None
        self.upload_url: str | None = None
        self.upload_result: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None

    def start_scan(self, film_format: str = "standard", upload_url: str | None = None) -> None:
        """
        Start a new scanning operation.

        Args:
            film_format: Transitional profile label. Phase 1 uses "standard".

        Raises:
            RuntimeError: If a scan is already in progress
        """
        with self._lock:
            if self.state != ScannerState.IDLE:
                raise RuntimeError("Scan already in progress")

            self.state = ScannerState.SCANNING
            self.current_row = 0
            self.current_col = 0
            self.scan_order_index = 0
            self.cancel_flag = False
            self.error_message = ""
            self.integration_result = None
            self.upload_url = upload_url
            self.upload_result = None

            # Prepare captures directory
            if CAPTURES_DIR.exists():
                shutil.rmtree(CAPTURES_DIR)
            CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

            self._scan_thread = threading.Thread(
                target=self._run_scan, args=(film_format,), daemon=True
            )
            self._scan_thread.start()

    def cancel_scan(self) -> None:
        """Request cancellation of the current scan.

        The scan thread owns homing. Do not set a terminal state here; otherwise
        the thread can return early and leave the motors away from origin.
        """
        with self._lock:
            if self.state in (
                ScannerState.SCANNING,
                ScannerState.STITCHING,
                ScannerState.UPLOADING,
            ):
                self.cancel_flag = True
                self.error_message = ""

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
                "artifacts": self.integration_result or {},
                "upload": self.upload_result or {},
            }

            if self.state == ScannerState.ERROR:
                status["error"] = self.error_message

            return status

    def reset_state(self) -> None:
        """Clear a terminal scanner state after an error or cancellation.

        This is intentionally conservative: it never moves motors and only
        succeeds when the scanner is not active, motors are stopped, and both
        axes are already at the configured home position.
        """
        with self._lock:
            if self.state in (
                ScannerState.SCANNING,
                ScannerState.STITCHING,
                ScannerState.UPLOADING,
                ScannerState.RETURNING_HOME,
            ):
                raise RuntimeError("Cannot reset while scanner is active")
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Cannot reset while motor is moving")
        x_position, y_position = self.scanner.get_position()
        if x_position != 0 or y_position != 0:
            raise RuntimeError("Cannot reset until motors are at home position")
        with self._lock:
            self.state = ScannerState.IDLE
            self.current_row = 0
            self.current_col = 0
            self.scan_order_index = 0
            self.cancel_flag = False
            self.error_message = ""
            self.upload_url = None
            self.upload_result = None

    def move_motors(self, x_steps: int, y_steps: int) -> None:
        """
        Move motors by specified steps.

        Raises RuntimeError if any motor is currently moving.
        """
        if self.state != ScannerState.IDLE:
            raise RuntimeError("Scanner is not idle")
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Motor is moving")

        if x_steps != 0:
            self.scanner.move_x(x_steps)
        if y_steps != 0:
            self.scanner.move_y(y_steps)

    def move_to_position(self, x_position: int, y_position: int) -> None:
        """Move motors to an absolute position in scanner coordinates."""
        if self.state != ScannerState.IDLE:
            raise RuntimeError("Scanner is not idle")
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Motor is moving")
        self.scanner.move_to(int(x_position), int(y_position))

    def home_motors(self) -> None:
        """
        Return motors to home/origin position.

        This operation waits for any current motion to complete.
        """
        if self.state not in (ScannerState.IDLE, ScannerState.CANCELLED, ScannerState.ERROR):
            raise RuntimeError("Scanner is not idle")
        self.scanner.return_to_origin()

    def set_home_motors(self) -> None:
        """
        Set current motor position as home (origin).

        Raises RuntimeError if any motor is currently moving.
        """
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Motor is moving")
        self.scanner.set_home()

    def get_position(self) -> tuple[int, int]:
        """Return current motor position in scanner coordinates."""
        return self.scanner.get_position()

    def capture_preview(self, path: Path) -> None:
        """Capture a display preview frame while the scanner is idle."""
        if self.state != ScannerState.IDLE:
            raise RuntimeError("Scanner is not idle")
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Motor is moving")
        self.scanner.capture_preview(path)

    def capture_calibration_frame(self, raw_path: Path, preview_path: Path) -> dict[str, Any]:
        """Capture one RAW+preview calibration frame at the current position."""
        if self.state != ScannerState.IDLE:
            raise RuntimeError("Scanner is not idle")
        if self.scanner.is_any_motor_moving():
            raise RuntimeError("Motor is moving")
        self.scanner.capture_to_files(raw_path, preview_path)
        x_position, y_position = self.scanner.get_position()
        return {
            "raw_path": str(raw_path),
            "preview_path": str(preview_path),
            "raw_size": raw_path.stat().st_size if raw_path.exists() else 0,
            "preview_size": preview_path.stat().st_size if preview_path.exists() else 0,
            "position": {"x": int(x_position), "y": int(y_position)},
        }

    def _cancel_requested(self) -> bool:
        with self._lock:
            return self.cancel_flag

    def _calculate_progress(self) -> float:
        """Calculate scan completion percentage."""
        if self.state not in [
            ScannerState.SCANNING,
            ScannerState.STITCHING,
            ScannerState.UPLOADING,
            ScannerState.RETURNING_HOME,
        ]:
            return 0.0

        total_positions = X_SEGMENTS * Y_SEGMENTS

        if self.state == ScannerState.RETURNING_HOME:
            return 100.0
        elif self.state == ScannerState.STITCHING:
            return 92.0
        elif self.state == ScannerState.UPLOADING:
            return 96.0
        else:
            return min(100.0, self.scan_order_index / total_positions * 100.0)

    def _run_scan(self, film_format: str) -> None:
        """
        Execute the scanning operation in a background thread.

        Performs serpentine scanning pattern across the film.
        """
        try:
            # Start moving right
            direction = 1
            scan_cancelled = False

            for row in range(Y_SEGMENTS):
                with self._lock:
                    self.current_row = row

                for scan_col in range(X_SEGMENTS):
                    if self._cancel_requested():
                        scan_cancelled = True
                        break

                    # The scanner moves in a serpentine path. On right-to-left
                    # rows the physical column decreases, so capture filenames
                    # must use the physical grid column rather than loop order.
                    physical_col = scan_col if direction else X_SEGMENTS - 1 - scan_col
                    with self._lock:
                        self.current_col = physical_col
                        self.scan_order_index = row * X_SEGMENTS + scan_col

                    # Capture at each grid point
                    self.scanner.capture(row, physical_col)
                    with self._lock:
                        self.scan_order_index = row * X_SEGMENTS + scan_col + 1

                    if self._cancel_requested():
                        scan_cancelled = True
                        break

                    # Move X except at end of row
                    if scan_col < X_SEGMENTS - 1:
                        steps = X_STEPS_PER_SEG if direction else -X_STEPS_PER_SEG
                        self.scanner.move_x(steps)

                    if self._cancel_requested():
                        scan_cancelled = True
                        break

                if scan_cancelled:
                    break

                # Move Y down except last row
                if row < Y_SEGMENTS - 1:
                    self.scanner.move_y(Y_STEPS_PER_SEG)

                if self._cancel_requested():
                    scan_cancelled = True
                    break

                # Reverse X direction for serpentine pattern
                direction ^= 1

            completion_error: Exception | None = None
            if scan_cancelled:
                with self._lock:
                    self.upload_result = {"status": "cancelled_returning_home"}
            else:
                try:
                    with self._lock:
                        self.state = ScannerState.STITCHING
                    integration_result = integrate_captures(CAPTURES_DIR)
                    with self._lock:
                        self.integration_result = integration_result

                    if self._cancel_requested():
                        with self._lock:
                            self.upload_result = {"status": "cancelled_returning_home"}
                    elif self.upload_url:
                        with self._lock:
                            self.state = ScannerState.UPLOADING
                        upload_result = self._upload_stitched_raw(self.upload_url, integration_result)
                        with self._lock:
                            self.upload_result = upload_result
                except Exception as exc:
                    completion_error = exc

            # Return to origin
            with self._lock:
                self.state = ScannerState.RETURNING_HOME

            try:
                self.scanner.return_to_origin()
            except Exception as exc:
                completion_error = exc

            # Complete
            with self._lock:
                self.current_row = 0
                self.current_col = 0
                self.scan_order_index = 0
                self.cancel_flag = False
                if completion_error is not None:
                    self.state = ScannerState.ERROR
                    self.error_message = str(completion_error)
                else:
                    self.state = ScannerState.IDLE

        except Exception as e:
            with self._lock:
                self.state = ScannerState.ERROR
                self.error_message = str(e)

    def _upload_stitched_raw(self, upload_url: str, integration_result: dict[str, Any]) -> dict[str, Any]:
        raw_path_str = integration_result.get("stitched_raw_path")
        if not raw_path_str:
            raise RuntimeError("No stitched raw DNG artifact available for upload")
        raw_path = Path(raw_path_str)
        if not raw_path.exists():
            raise RuntimeError(f"Stitched raw DNG does not exist: {raw_path}")

        if _requests is None:
            raise RuntimeError("Upload requires requests; install it in the scanner venv")

        file_size = raw_path.stat().st_size
        body, content_type = _multipart_file_stream("file", raw_path)
        started = time.monotonic()
        print(
            f"Uploading stitched RAW {raw_path.name} ({file_size / (1024 * 1024):.1f} MiB) "
            f"to {upload_url}"
        )
        try:
            resp = _requests.post(
                upload_url,
                data=body,
                headers={"Content-Type": content_type},
                timeout=(10, _UPLOAD_TIMEOUT_S),
            )
            resp.raise_for_status()
        except _requests.RequestException as exc:
            detail = ""
            response = getattr(exc, "response", None)
            if response is not None:
                detail = f" HTTP {response.status_code}: {response.text[:500]}"
            raise RuntimeError(f"Upload failed: {exc}{detail}") from exc

        elapsed = max(time.monotonic() - started, 1e-6)
        print(
            f"Uploaded stitched RAW in {elapsed:.1f}s "
            f"({file_size / (1024 * 1024) / elapsed:.2f} MiB/s)"
        )
        return {
            "status": "uploaded",
            "url": upload_url,
            "http_status": resp.status_code,
            "bytes": file_size,
            "elapsed_s": elapsed,
            "response": resp.text,
        }

    def cleanup(self) -> None:
        """Clean up resources."""
        self.scanner.cleanup()


def _multipart_file_stream(field_name: str, path: Path) -> tuple["_MultipartFileStream", str]:
    boundary = f"----film-digitizer-{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode()
    footer = f"\r\n--{boundary}--\r\n".encode()
    return _MultipartFileStream(path=path, header=header, footer=footer), f"multipart/form-data; boundary={boundary}"


class _MultipartFileStream:
    """Streaming multipart body with a known length for requests.

    A plain generator makes requests use chunked transfer encoding, which some
    ASGI servers reject for multipart uploads. Providing __len__ lets requests
    send a fixed Content-Length while still reading the DNG from disk in chunks.
    """

    def __init__(self, path: Path, header: bytes, footer: bytes):
        self.path = path
        self.header = header
        self.footer = footer
        self.file_size = path.stat().st_size

    def __len__(self) -> int:
        return len(self.header) + self.file_size + len(self.footer)

    def __iter__(self):
        yield self.header
        uploaded = 0
        next_log = _UPLOAD_LOG_INTERVAL
        total = self.file_size
        with self.path.open("rb") as fh:
            while True:
                chunk = fh.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                uploaded += len(chunk)
                if uploaded >= next_log or uploaded == total:
                    print(
                        f"Upload progress: {uploaded / (1024 * 1024):.1f}/"
                        f"{total / (1024 * 1024):.1f} MiB"
                    )
                    next_log += _UPLOAD_LOG_INTERVAL
                yield chunk
        yield self.footer
