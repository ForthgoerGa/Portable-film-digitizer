import time
import io
from PIL import Image
from uploader import Uploader


class Stitcher:
    def __init__(self):
        self.uploader = Uploader()

    def stitch(self, frames):
        print(f"[MockStitcher] Stitching frames: {frames}...")
        time.sleep(1.2)
        # Mock: create a dummy image in memory
        img = Image.new("RGB", (1920, 1080), color=(73, 109, 137))  # Placeholder image
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)
        print(f"[MockStitcher] Generated stitched image in memory")
        return img_bytes.getvalue()

    def upload(self, frames):
        stitched_bytes = self.stitch(frames)
        return self.uploader.upload(stitched_bytes)
