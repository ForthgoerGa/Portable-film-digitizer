import sys
from serial.tools import miniterm

def start_console():
    # Configuration - Change these to match your setup
    port = '/dev/serial0'
    baud = 115200
    
    # Initialize miniterm with your settings
    # 'exit_char' is set to 29, which is Ctrl + ]
    sys.argv = [sys.argv[0], port, str(baud)]
    
    try:
        miniterm.main(default_port=port, default_baudrate=baud)
    except KeyboardInterrupt:
        print("\nConsole stopped by user.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    start_console()
