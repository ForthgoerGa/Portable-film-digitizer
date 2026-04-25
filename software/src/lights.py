import board
import neopixel
import time
import random

class LEDStrip:
    def __init__(self, pin=board.D12, num_pixels=8, brightness=0.2):
        """Initializes the strip with a default safety brightness."""
        self.strip = neopixel.NeoPixel(
            pin,
            num_pixels,
            brightness=brightness,
            auto_write=True
        )
        self.num_pixels = num_pixels

    def set_color(self, index, r, g, b):
        """Sets a specific pixel to an (R, G, B) color."""
        if 0 <= index < self.num_pixels:
            self.strip[index] = (r, g, b)
        else:
            print(f"Index {index} out of range.")

    def fill(self, r, g, b):
        """Fills the entire strip with one color."""
        self.strip.fill((r, g, b))

    def clear(self):
        """Turns all LEDs off."""
        self.fill(0, 0, 0)

    def set_brightness(self, level):
        """Adjusts brightness (0.0 to 1.0)."""
        self.strip.brightness = level

    # --- New Fun Methods ---

    def _wheel(self, pos):
        """Helper to generate rainbow colors across 0-255 spectrum."""
        if pos < 0 or pos > 255:
            return (0, 0, 0)
        if pos < 85:
            return (255 - pos * 3, pos * 3, 0)
        if pos < 170:
            pos -= 85
            return (0, 255 - pos * 3, pos * 3)
        pos -= 170
        return (pos * 3, 0, 255 - pos * 3)

    def rainbow_cycle(self, wait=0.001, iterations=1):
        """Draws a rainbow that uniformly distributes itself across the entire strip."""
        for j in range(256 * iterations):
            for i in range(self.num_pixels):
                pixel_index = (i * 256 // self.num_pixels) + j
                self.strip[i] = self._wheel(pixel_index & 255)
            time.sleep(wait)

    def theater_chase(self, r, g, b, wait=0.1, iterations=10):
        """Movie theater light style chasing animation."""
        for _ in range(iterations):
            for q in range(3):
                for i in range(0, self.num_pixels, 3):
                    if i + q < self.num_pixels:
                        self.strip[i + q] = (r, g, b)
                time.sleep(wait)
                for i in range(0, self.num_pixels, 3):
                    if i + q < self.num_pixels:
                        self.strip[i + q] = (0, 0, 0)

    def comet(self, r, g, b, speed=0.05, tail_length=3):
        """Shoots a color 'comet' down the strip with a fading tail."""
        for i in range(self.num_pixels + tail_length):
            self.clear()
            for j in range(tail_length):
                idx = i - j
                if 0 <= idx < self.num_pixels:
                    # Dim the tail as it goes back
                    factor = (tail_length - j) / tail_length
                    self.strip[idx] = (int(r * factor), int(g * factor), int(b * factor))
            time.sleep(speed)

    def strobe(self, r, g, b, count=5, speed=0.05):
        """Flashing strobe light effect."""
        for _ in range(count):
            self.fill(r, g, b)
            time.sleep(speed)
            self.clear()
            time.sleep(speed)

    def sparkle(self, r, g, b, count=20, speed=0.05):
        """Randomly blinks individual pixels."""
        for _ in range(count):
            idx = random.randint(0, self.num_pixels - 1)
            self.strip[idx] = (r, g, b)
            time.sleep(speed)
            self.strip[idx] = (0, 0, 0)
