import time
import threading
import RPi.GPIO as GPIO

# ----------------------------
# GPIO SETUP
# ----------------------------
STEP_X = 20
STEP_Y = 21

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(STEP_X, GPIO.OUT)
GPIO.setup(STEP_Y, GPIO.OUT)

GPIO.output(STEP_X, GPIO.LOW)
GPIO.output(STEP_Y, GPIO.LOW)

# ----------------------------
# CONFIG
# ----------------------------
RPM = 120  # adjust as needed
SPR = 200  # steps per revolution (typical stepper)
step_delay = 0.005  # steady-state delay fallback

# ----------------------------
# STATE
# ----------------------------
running_x = False
running_y = False

# ----------------------------
# CORE STEP FUNCTION
# ----------------------------
def step_once(pin, delay):
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(delay)
    GPIO.output(pin, GPIO.LOW)
    time.sleep(delay)

# ----------------------------
# RAMP GENERATOR
# ----------------------------
def ramp_up():
    target_delay = 60 / (RPM * SPR * 2)
    delay = 0.01

    while delay > target_delay:
        delay *= 0.98
        yield delay

# ----------------------------
# MOTOR LOOPS
# ----------------------------
def motor_x_loop():
    global running_x

    while True:
        if running_x:
            for d in ramp_up():
                if not running_x:
                    break
                step_once(STEP_X, d)

            while running_x:
                step_once(STEP_X, step_delay)
        else:
            GPIO.output(STEP_X, GPIO.LOW)
            time.sleep(0.01)


def motor_y_loop():
    global running_y

    while True:
        if running_y:
            for d in ramp_up():
                if not running_y:
                    break
                step_once(STEP_Y, d)

            while running_y:
                step_once(STEP_Y, step_delay)
        else:
            GPIO.output(STEP_Y, GPIO.LOW)
            time.sleep(0.01)

# ----------------------------
# INPUT CONTROL LOOP
# ----------------------------
def input_loop():
    global running_x, running_y

    print("Controls:")
    print("  x -> toggle motor X")
    print("  y -> toggle motor Y")
    print("  q -> quit")

    while True:
        cmd = input("> ").strip().lower()

        if cmd == "x":
            running_x = not running_x
            print("Motor X:", "ON" if running_x else "OFF")

        elif cmd == "y":
            running_y = not running_y
            print("Motor Y:", "ON" if running_y else "OFF")

        elif cmd == "q":
            print("Exiting...")
            running_x = False
            running_y = False
            GPIO.cleanup()
            break

# ----------------------------
# START THREADS
# ----------------------------
threading.Thread(target=motor_x_loop, daemon=True).start()
threading.Thread(target=motor_y_loop, daemon=True).start()
threading.Thread(target=input_loop, daemon=False).start()
