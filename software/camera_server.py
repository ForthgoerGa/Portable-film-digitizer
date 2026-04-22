import time
import threading
import cv2
from flask import Flask, Response


class CameraServer:
    def __init__(self):
        self.latest_frame = None
        self.lock = threading.Lock()

        # ----------------------------
        # CAMERA (GSTREAMER + RPiCam)
        # ----------------------------
        self.camera = cv2.VideoCapture(
        "libcamerasrc ! videoconvert ! video/x-raw,format=BGR ! "
        "videoscale ! video/x-raw,width=640,height=480 ! "
        "appsink drop=1",
        cv2.CAP_GSTREAMER
        )

        if not self.camera.isOpened():
            raise RuntimeError(
                "Camera failed to open. Check GStreamer + libcamera installation."
            )

        # ----------------------------
        # FLASK APP
        # ----------------------------
        self.app = Flask(__name__)
        self.app.add_url_rule("/", "index", self.index)
        self.app.add_url_rule("/video_feed", "video_feed", self.video_feed)

    # ----------------------------
    # CAMERA THREAD
    # ----------------------------
    def camera_loop(self):
        while True:
            ret, frame = self.camera.read()

            if not ret:
                time.sleep(0.01)
                continue

            with self.lock:
                self.latest_frame = frame

    # ----------------------------
    # MJPEG STREAM
    # ----------------------------
    def generate_frames(self):
        while True:
            with self.lock:
                if self.latest_frame is None:
                    continue

                _, buffer = cv2.imencode(".jpg", self.latest_frame)
                frame = buffer.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                frame +
                b"\r\n"
            )

    # ----------------------------
    # WEB UI
    # ----------------------------
    def index(self):
        return """
        <html>
            <head><title>Pi HQ Camera</title></head>
            <body>
                <h2>Live Camera Feed</h2>
                <img src="/video_feed" width="960"/>
            </body>
        </html>
        """

    def video_feed(self):
        return Response(
            self.generate_frames(),
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )

    # ----------------------------
    # START SERVER
    # ----------------------------
    def start(self, host="0.0.0.0", port=5000):
        threading.Thread(target=self.camera_loop, daemon=True).start()

        threading.Thread(
            target=lambda: self.app.run(
                host=host,
                port=port,
                debug=False,
                use_reloader=False
            ),
            daemon=True
        ).start()

    # ----------------------------
    # CLEANUP
    # ----------------------------
    def release(self):
        self.camera.release()


# =========================================================
# TEST ENTRYPOINT
# =========================================================
if __name__ == "__main__":
    print("Starting HQ camera server (rpicam + GStreamer)...")

    cam = CameraServer()
    cam.start()

    print("Server running at http://127.0.0.1:5000")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        cam.release()
