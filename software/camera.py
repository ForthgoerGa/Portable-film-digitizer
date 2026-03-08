import time


class CameraInterface:
    def capture(self, idx=0):
        print(f"[MockCamera] Capturing frame {idx}...")
        time.sleep(1)
        print(f"[MockCamera] Frame {idx} captured.")
