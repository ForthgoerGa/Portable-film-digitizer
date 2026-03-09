from __future__ import annotations

import tempfile
import threading
import time
from enum import Enum, auto
from pathlib import Path

import cv2
import serial_comm
from serial_comm import MockSerialInterface
from stitcher import Stitcher, MockScanner
from uploader import Uploader


class ScanState(Enum):
    ready = auto()
    working = auto()
    error = auto()


class ScanJobCoordinator:
    def __init__(self):
        self.state = ScanState.ready
        self.result = None
        self.serial = None
        self.stitcher = Stitcher()
        self.cancel_flag = False
        self._last_format = None
        self._job_thread = None
        self._lock = threading.Lock()

    def start_job(self, film_format):
        with self._lock:
            if self.state != ScanState.ready:
                raise RuntimeError("Job already running or not ready")

            self.state = ScanState.working
            self.result = None
            self.cancel_flag = False
            self._last_format = film_format

            self._job_thread = threading.Thread(
                target=self.run_job,
                args=(film_format,),
                daemon=True,
            )
            self._job_thread.start()

    def run_job(self, film_format):
        try:
            # Mock scanning loop
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_tiles_dir = Path(temp_dir)
                mock_scanner = MockScanner()
                while mock_scanner.simulate_next_tile(temp_tiles_dir):
                    if self.cancel_flag:
                        with self._lock:
                            self.state = ScanState.ready
                            self.result = {"cancelled": True}
                        return

                # Process the scanned tiles
                scan_payload = self.stitcher.process_scan(temp_tiles_dir)

                # Upload the final image
                final_image_path = self.stitcher.output_dir / "processed_negative.png"
                image = cv2.imread(str(final_image_path))
                height, width = image.shape[:2]
                if width > 2000 or height > 2000:
                    scale = min(2000 / width, 2000 / height)
                    new_width = int(width * scale)
                    new_height = int(height * scale)
                    image = cv2.resize(image, (new_width, new_height))
                _, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                image_bytes = buffer.tobytes()
                uploader = Uploader()
                cloud_url = uploader.upload(image_bytes)
                scan_payload["cloud_url"] = cloud_url

            if self.cancel_flag:
                with self._lock:
                    self.state = ScanState.ready
                    self.result = {"cancelled": True}
                return

            with self._lock:
                self.result = {
                    "format": film_format,
                    "completed_at": time.time(),
                    **scan_payload,
                }
                self.state = ScanState.ready
        except Exception as e:
            with self._lock:
                self.state = ScanState.error
                self.result = {"error": str(e)}
        finally:
            pass  # No serial to turn off

    def cancel_job(self):
        with self._lock:
            if self.state == ScanState.working:
                self.cancel_flag = True

    def reset_job(self):
        with self._lock:
            self.state = ScanState.ready
            self.result = None
            self.cancel_flag = False
            self._last_format = None

    def status_dict(self):
        with self._lock:
            return {
                "state": self.state.name,
                "format": self._last_format,
                "result": self.result,
            }


coordinator = ScanJobCoordinator()
