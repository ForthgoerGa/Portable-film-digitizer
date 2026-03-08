from __future__ import annotations

import threading
import time
from enum import Enum, auto

import serial_comm
from serial_comm import MockSerialInterface
from stitcher import Stitcher


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
            self.serial = (
                serial_comm.serial_instance
                if serial_comm.serial_instance is not None
                and serial_comm.serial_instance.is_open
                else MockSerialInterface()
            )
            self.serial.set_illumination(True)

            if self.cancel_flag:
                with self._lock:
                    self.state = ScanState.ready
                    self.result = {"cancelled": True}
                return

            scan_payload = self.stitcher.process_demo_scan()

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
            try:
                if self.serial is not None:
                    self.serial.set_illumination(False)
            except Exception:
                pass

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
