from enum import Enum, auto
from camera import CameraInterface
from serial_comm import get_serial, serial_instance
from stitcher import Stitcher


class ScanState(Enum):
    ready = auto()
    working = auto()
    error = auto()


class ScanJobCoordinator:
    def __init__(self):
        self.state = ScanState.ready
        self.result = None
        self.camera = CameraInterface()
        self.serial = None  # Set by job thread when a serial port is connected
        self.stitcher = Stitcher()
        self.cancel_flag = False
        self._last_format = None

    def start_job(self, film_format):
        if self.state == ScanState.error:
            self.reset_job()
        # Check serial connection before starting
        from serial_comm import serial_instance

        if serial_instance is None or not serial_instance.is_open:
            raise RuntimeError(
                "No serial connection. Please connect a serial port first."
            )
        if self.state != ScanState.ready:
            raise RuntimeError("Job already running or not ready")
        self.state = ScanState.working
        self.result = None
        self.cancel_flag = False
        self._last_format = film_format
        self.run_job(film_format)

    def run_job(self, film_format):
        try:
            from serial_comm import serial_instance

            self.serial = serial_instance
            self.serial.set_illumination(True)
            for i in range(2):
                if self.cancel_flag:
                    self.state = ScanState.ready
                    self.result = {"cancelled": True}
                    return
                self.serial.move(i * 10, 0)
                if self.cancel_flag:
                    self.state = ScanState.ready
                    self.result = {"cancelled": True}
                    return
                self.camera.capture(i)
                if self.cancel_flag:
                    self.state = ScanState.ready
                    self.result = {"cancelled": True}
                    return
            self.serial.set_illumination(False)
            if self.cancel_flag:
                self.state = ScanState.ready
                self.result = {"cancelled": True}
                return
            url = self.stitcher.upload([f"frame_{k}.jpg" for k in range(2)])
            if self.cancel_flag:
                self.state = ScanState.ready
                self.result = {"cancelled": True}
                return
            self.result = {
                "url": url,
                "format": film_format,
            }
            self.state = ScanState.ready
        except Exception as e:
            self.state = ScanState.error
            self.result = {"error": str(e)}

    def cancel_job(self):
        if self.state == ScanState.working:
            self.cancel_flag = True

    def reset_job(self):
        self.state = ScanState.ready
        self.result = None
        self.cancel_flag = False
        self._last_format = None

    def status_dict(self):
        return {
            "state": self.state.name,
            "format": self._last_format,
            "result": self.result,
        }


coordinator = ScanJobCoordinator()
