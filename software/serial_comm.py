import threading

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

BAUD_RATE = 115200


class SerialInterface:
    def __init__(self, port: str, baudrate=BAUD_RATE, timeout=1.0):
        if serial is None:
            raise RuntimeError("pyserial is not installed.")
        self._lock = threading.Lock()
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = serial.Serial(
            port=self._port, baudrate=self._baudrate, timeout=self._timeout
        )
        self._listening = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def send_command(self, cmd: str):
        """Send ASCII command string to embedded device."""
        print(f"Sending command: {cmd.strip()}")
        with self._lock:
            self._ser.write(cmd.encode("ascii"))
            # No response expected per embedded protocol

    def _listen_loop(self):
        while self._listening:
            with self._lock:
                try:
                    data = self._ser.readline()
                    if data:
                        decoded = data.decode("ascii").strip()
                        if decoded:
                            print(f"Received: {decoded}")
                except (UnicodeDecodeError, serial.SerialException):
                    pass  # Skip malformed data or serial errors

    def move(self, x: int, y: int):
        cmd_x = f"$MX:{x};"
        cmd_y = f"$MY:{y};"
        self.send_command(cmd_x)
        self.send_command(cmd_y)

    def jog_forward(self):
        self.send_command("w")

    def jog_reverse(self):
        self.send_command("s")

    def stop(self):
        self.send_command("q")

    def close(self):
        self._listening = False
        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._ser and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self):
        return self._ser.is_open


serial_instance = None
try:
    serial_instance = SerialInterface("/dev/ttyAMA0")
except Exception:
    pass


def get_serial():
    return serial_instance


if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyAMA0"
    s = SerialInterface(port)
    print("Moving X to 42, Y to 0...")
    s.move(42, 0)
    s.close()
    print("Done.")
