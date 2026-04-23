# Constants commonly used in RPi.GPIO
BOARD = "BOARD"
BCM = "BCM"
OUT = "OUT"
IN = "IN"
HIGH = 1
LOW = 0

def setup(*args, **kwargs):
    print(f"Mock GPIO: Setup called with {args}")

def output(channel, state):
    pass
    # print(f"Mock GPIO: Output on {channel} set to {state}")

def cleanup():
    print("Mock GPIO: Cleanup called")

# The "Magic" Catch-All for the module level
def __getattr__(name):
    def dummy_method(*args, **kwargs):
        print(f"Mock GPIO: Called undefined method '{name}' (Ignoring)")
        return None
    return dummy_method
