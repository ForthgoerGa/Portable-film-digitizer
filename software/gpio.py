import RPi.GPIO as GPIO
import time
import threading

# --- Configuration ---
STEP = 20
DIR = 21
SPR = 800
RPM = 60

step_delay = 60 / (RPM * SPR * 2)

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP, GPIO.OUT)
GPIO.setup(DIR, GPIO.OUT)

GPIO.output(DIR, GPIO.HIGH)

running = False
lock = threading.Lock()

def stepper_loop():
    global running
    while True:
        with lock:
            is_running = running

        if is_running:
            GPIO.output(STEP, GPIO.HIGH)
            time.sleep(step_delay)
            GPIO.output(STEP, GPIO.LOW)
            time.sleep(step_delay)
        else:
            GPIO.output(STEP, GPIO.LOW)
            time.sleep(0.01)

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
