import serial
import struct
import threading
import time
from serial.tools import list_ports

BAUD_RATE = 115200


class SerialInterface:
    START_BYTE = 0x02
    END_BYTE = 0x03
    ACK = 0x06
    NACK = 0x15

    def __init__(self, port: str, baudrate=BAUD_RATE, timeout=1.0):
        self._lock = threading.Lock()
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = serial.Serial(
            port=self._port, baudrate=self._baudrate, timeout=self._timeout
        )

    def send_packet(self, cmd: int, data_bytes: bytes = b""):
        packet = bytearray([self.START_BYTE, cmd]) + data_bytes
        chksum = (cmd + sum(data_bytes)) % 256
        packet.append(chksum)
        packet.append(self.END_BYTE)

        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(packet)
            resp = self._ser.read(1)
            if not resp:
                raise TimeoutError("No response from slave device (timeout)")
            if resp[0] == self.ACK:
                return True
            elif resp[0] == self.NACK:
                raise IOError("Slave sent NACK")
            else:
                raise ValueError(f"Unexpected response: {resp.hex()}")

    def move(self, x: int, y: int):
        data = struct.pack("<HH", x, y)
        return self.send_packet(cmd=0x10, data_bytes=data)

    def set_illumination(self, on: bool):
        data = bytes([0x01 if on else 0x00])
        return self.send_packet(cmd=0x20, data_bytes=data)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self):
        return self._ser.is_open


class MockSerialInterface:
    def move(self, x: int, y: int):
        print(f"[MockSerial] Moving to position ({x}, {y})...")
        time.sleep(0.5)
        print(f"[MockSerial] Arrived at position ({x}, {y}).")

    def set_illumination(self, on: bool):
        print(f"[MockSerial] Illumination {'on' if on else 'off'}...")
        time.sleep(0.2)

    @property
    def is_open(self):
        return True

    def close(self):
        pass


serial_instance = None
serial_error = None


def get_serial():
    return serial_instance


def connect_serial(port: str):
    global serial_instance, serial_error
    try:
        if serial_instance is not None and serial_instance.is_open:
            serial_instance.close()
        if port == "MOCK":
            serial_instance = MockSerialInterface()
        else:
            serial_instance = SerialInterface(port)
        serial_error = None
        return {"connected": True, "port": port, "error": None}
    except Exception as e:
        serial_instance = None
        serial_error = str(e)
        return {"connected": False, "port": None, "error": serial_error}


def disconnect_serial():
    global serial_instance
    if serial_instance is not None:
        serial_instance.close()
        serial_instance = None
    return {"connected": False, "port": None, "error": None}


def get_serial_status():
    if serial_instance is None:
        return {"connected": False, "port": None, "error": serial_error}
    port = getattr(serial_instance, "_port", None)
    if port is None:
        port = "MOCK"
    return {
        "connected": serial_instance.is_open,
        "port": port,
        "error": serial_error,
    }


def list_serial_ports():
    ports = [{"port": "MOCK", "description": "Mock Port (testing)"}]
    ports.extend(
        [
            {"port": p.device, "description": p.description}
            for p in list_ports.comports()
        ]
    )
    return ports


if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    s = SerialInterface(port)
    print("Moving to frame 42...")
    s.move(42, 0)
    print("Setting illumination ON...")
    s.set_illumination(True)
    s.close()
    print("Done.")
