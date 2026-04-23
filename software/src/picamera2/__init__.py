class Picamera2:
    def __init__(self, *args, **kwargs):
        print("Mock Picamera2: System Initialized")

    def create_preview_configuration(self, **kwargs):
        print(f"Mock Picamera2: Creating preview config with {kwargs}")
        return "mock_config"

    def create_still_configuration(self, **kwargs):
        return "mock_still_config"

    def configure(self, config):
        print(f"Mock Picamera2: Configured with {config}")

    def start(self):
        print("Mock Picamera2: Stream started")

    def capture_file(self, filename, **kwargs):
        print(f"Mock Picamera2: Captured image to {filename}")

    # The "Magic" Catch-All
    def __getattr__(self, name):
        """Returns a dummy function for any method not explicitly defined above."""
        def dummy_method(*args, **kwargs):
            print(f"Mock Picamera2: Called undefined method '{name}' (Ignoring)")
            return None
        return dummy_method
