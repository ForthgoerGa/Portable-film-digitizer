import time
import RPi.GPIO as GPIO

# ----------------------------
# GPIO SETUP
# ----------------------------
STEP_X = 21
STEP_Y = 20

DIR_X = 26
DIR_Y = 19

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(STEP_X, GPIO.OUT)
GPIO.setup(STEP_Y, GPIO.OUT)
GPIO.setup(DIR_X, GPIO.OUT)
GPIO.setup(DIR_Y, GPIO.OUT)

GPIO.output(STEP_X, GPIO.LOW)
GPIO.output(STEP_Y, GPIO.LOW)

# ----------------------------
# CONFIG
# ----------------------------
RPM = 200                    # realistic speed
SPR = 200 * 16               # steps per revolution (microstepping)

STEP_DELAY = 0.00001         # ~2 kHz stepping (stable in Python)

RAMP_START = 0.002
RAMP_FACTOR = 0.90

# Scan parameters
X_SEGMENTS = 4
Y_SEGMENTS = 2

X_STEPS_PER_SEG = 20000
Y_STEPS_PER_SEG = 8000


# ----------------------------
# MOTOR CLASS
# ----------------------------
class StepperMotor:
    def __init__(self, step_pin, dir_pin, invert_dir=False):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.invert_dir = invert_dir
        self.direction = 1  # 1 = forward, 0 = backward

    def set_direction(self, direction):
        self.direction = direction
        value = GPIO.HIGH if direction else GPIO.LOW

        if self.invert_dir:
            value = GPIO.LOW if value == GPIO.HIGH else GPIO.HIGH

        GPIO.output(self.dir_pin, value)

    def step(self, delay):
        GPIO.output(self.step_pin, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(self.step_pin, GPIO.LOW)
        time.sleep(delay)

    def ramp_profile(self, steps, target_delay):
        ramp = []
        delay = RAMP_START

        while delay > target_delay:
            ramp.append(delay)
            delay *= RAMP_FACTOR

        ramp_len = min(len(ramp), steps // 2)
        return ramp[:ramp_len]

    def move_steps(self, steps, target_delay):
        ramp = self.ramp_profile(steps, target_delay)
        ramp_len = len(ramp)

        # accelerate
        for d in ramp:
            self.step(d)

        # constant speed
        for _ in range(steps - 2 * ramp_len):
            self.step(target_delay)

        # decelerate
        for d in reversed(ramp):
            self.step(d)


# ----------------------------
# CAMERA HOOK
# ----------------------------
def capture_image(row, col):
    """
    Replace this with your actual capture call.
    Example:
        subprocess.run(["libcamera-still", "-o", f"img_{row}_{col}.jpg"])
    """
    print(f"[CAPTURE] row={row}, col={col}")
    time.sleep(0.05)  # simulate capture delay


# ----------------------------
# SCANNER
# ----------------------------
class FilmScanner:
    def __init__(self, motor_x, motor_y):
        self.motor_x = motor_x
        self.motor_y = motor_y

    def scan(self):
        print("Starting serpentine scan...")

        # Start moving right
        direction = 1
        self.motor_x.set_direction(direction)

        for row in range(Y_SEGMENTS):
            print(f"Row {row}")

            for col in range(X_SEGMENTS):
                # Capture at each grid point
                capture_image(row, col)

                # Move X except at end
                if col < X_SEGMENTS - 1:
                    self.motor_x.move_steps(X_STEPS_PER_SEG, STEP_DELAY)

            # Move Y down except last row
            if row < Y_SEGMENTS - 1:
                self.motor_y.set_direction(1)  # DOWN
                self.motor_y.move_steps(Y_STEPS_PER_SEG, STEP_DELAY)

            # Reverse X direction
            direction ^= 1
            self.motor_x.set_direction(direction)

        print("Scan complete. Returning to origin...")
        self.return_to_origin()
        print("Done.")
    
    def return_to_origin(self):
        # Return X only if not already home
        if Y_SEGMENTS % 2 == 1:
            # ended on far side → need to come back
            self.motor_x.set_direction(0)  # left
            self.motor_x.move_steps(X_STEPS_PER_SEG * (X_SEGMENTS - 1), STEP_DELAY)

        # Always return Y
        self.motor_y.set_direction(0)  # up
        self.motor_y.move_steps(Y_STEPS_PER_SEG * (Y_SEGMENTS - 1), STEP_DELAY)



# ----------------------------
# MAIN
# ----------------------------
def main():
    motor_x = StepperMotor(STEP_X, DIR_X, invert_dir=True)
    motor_y = StepperMotor(STEP_Y, DIR_Y)

    scanner = FilmScanner(motor_x, motor_y)
    try:
        input("Press ENTER to start scan...")
        scanner.scan()

    except KeyboardInterrupt:
        print("Interrupted!")

    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
