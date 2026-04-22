import time
import threading
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
RPM = 500
SPR = 200 * 16

STEP_DELAY = 0.000001

RAMP_START = 0.01
RAMP_FACTOR = 0.98


# ----------------------------
# MOTOR CLASS
# ----------------------------
class StepperMotor:
    def __init__(self, step_pin, dir_pin, invert_dir=False):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.invert_dir = invert_dir

        self.running = False
        self.direction = 1  # 1 = forward, 0 = backward

    def set_direction(self, direction):
        self.direction = direction

    def set_gpio_dir(self):
        value = GPIO.HIGH if self.direction else GPIO.LOW

        if self.invert_dir:
            value = GPIO.LOW if value == GPIO.HIGH else GPIO.HIGH

        GPIO.output(self.dir_pin, value)

    def step(self, delay):
        GPIO.output(self.step_pin, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(self.step_pin, GPIO.LOW)
        time.sleep(delay)

    def ramp_up(self):
        target_delay = 60 / (RPM * SPR * 2)
        delay = RAMP_START

        while delay > target_delay:
            delay *= RAMP_FACTOR
            yield delay

    def loop(self):
        while True:
            if self.running:
                self.set_gpio_dir()

                # acceleration phase
                for d in self.ramp_up():
                    if not self.running:
                        break
                    self.set_gpio_dir()
                    self.step(d)

                # steady state
                while self.running:
                    self.set_gpio_dir()
                    self.step(STEP_DELAY)

            else:
                GPIO.output(self.step_pin, GPIO.LOW)
                time.sleep(0.01)


# ----------------------------
# CREATE MOTORS
# ----------------------------
motor_x = StepperMotor(STEP_X, DIR_X, invert_dir=True)  # fix reversed axis if needed
motor_y = StepperMotor(STEP_Y, DIR_Y)


# ----------------------------
# INPUT LOOP
# ----------------------------
def input_loop():
    print("Controls:")
    print("  w -> Y negative (move)")
    print("  s -> Y positive (move)")
    print("  a -> X negative (move)")
    print("  d -> X positive (move)")
    print("  ENTER (blank) -> STOP ALL MOTORS")
    print("  q -> quit")

    while True:
        cmd = input("> ").strip().lower()

        # ----------------------------
        # GLOBAL STOP
        # ----------------------------
        if cmd == "":
            motor_x.running = False
            motor_y.running = False
            print("STOP ALL MOTORS")

        elif cmd == "w":
            motor_y.set_direction(0)
            motor_y.running = True
            print("Y: backward")

        elif cmd == "s":
            motor_y.set_direction(1)
            motor_y.running = True
            print("Y: forward")

        elif cmd == "a":
            motor_x.set_direction(0)
            motor_x.running = True
            print("X: left")

        elif cmd == "d":
            motor_x.set_direction(1)
            motor_x.running = True
            print("X: right")

        elif cmd == "q":
            print("Exiting...")
            motor_x.running = False
            motor_y.running = False
            GPIO.cleanup()
            break


# ----------------------------
# START THREADS
# ----------------------------
threading.Thread(target=motor_x.loop, daemon=True).start()
threading.Thread(target=motor_y.loop, daemon=True).start()
threading.Thread(target=input_loop, daemon=False).start()
