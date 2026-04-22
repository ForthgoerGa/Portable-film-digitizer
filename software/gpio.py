import RPi.GPIO as GPIO
import time
import threading

# --- Configuration ---
STEP = 20
DIR = 21
SPR = 300 * 16
RPM = 200

step_delay = 60 / (RPM * SPR * 2)

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP, GPIO.OUT)
GPIO.setup(DIR, GPIO.OUT)

GPIO.output(DIR, GPIO.HIGH)

running = False
lock = threading.Lock()

def ramp_up():
    global step_delay
    target_delay = 60 / (RPM * SPR * 2)
    delay = 0.01  # start slow

    while delay > target_delay:
        delay *= 0.98
        yield delay

def stepper_loop():
    global running
    current_delay = step_delay

    while True:
        if running:
            # ramp once when starting
            for d in ramp_up():
                if not running:
                    break
                step_once(d)

            # steady state
            while running:
                step_once(step_delay)
        else:
            GPIO.output(STEP, GPIO.LOW)
            time.sleep(0.01)

def step_once(delay):
    GPIO.output(STEP, GPIO.HIGH)
    time.sleep(delay)
    GPIO.output(STEP, GPIO.LOW)
    time.sleep(delay)

# Start motor thread
threading.Thread(target=stepper_loop, daemon=True).start()

print("Press ENTER to toggle motor ON/OFF. Ctrl+C to exit.")

try:
    while True:
        input()  # waits for Enter
        with lock:
            running = not running
            state = "ON" if running else "OFF"
        print(f"Motor {state}")

except KeyboardInterrupt:
    print("\nStopping motor...")
finally:
    GPIO.cleanup()
