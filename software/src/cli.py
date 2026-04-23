#!/usr/bin/env python3
"""
Interactive CLI for the film digitizer scanner.

Connects to the REST API running at localhost:5000 and provides
commands to control the scanner, move motors, and monitor status.
"""

import sys
import json
import urllib.request
import urllib.error
import os
from typing import Optional

API_BASE = "http://localhost:5000"

TERMS = {
    "idle": "IDLE",
    "scanning": "SCANNING",
    "returning_home": "RETURNING",
    "cancelled": "CANCELLED",
    "error": "ERROR",
}

COLORS = {
    "reset": "\033[0m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "gray": "\033[90m",
}

STATE_COLORS = {
    "idle": "gray",
    "scanning": "blue",
    "returning_home": "yellow",
    "cancelled": "gray",
    "error": "red",
}


def color(text: str, color_name: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(color_name, '')}{text}{COLORS['reset']}"


def api_get(endpoint: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(f"{API_BASE}{endpoint}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to server - {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def api_post(endpoint: str, data: Optional[dict] = None) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            f"{API_BASE}{endpoint}",
            data=json.dumps(data).encode() if data else None,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to server - {e}")
        return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            print(f"Error: {err.get('detail', str(e))}")
        except:
            print(f"Error: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def print_status(status: dict) -> None:
    state = status.get("state", "unknown")
    color_name = STATE_COLORS.get(state, "gray")

    state_str = color(TERMS.get(state, state.upper()), color_name)
    progress = status.get("progress", 0)
    row = status.get("current_row", 0)
    col = status.get("current_col", 0)
    total_rows = status.get("total_rows", 0)
    total_cols = status.get("total_cols", 0)

    print(
        f"State: {state_str} | Progress: {progress:.1f}% | Pos: ({col},{row})/{total_cols}x{total_rows}"
    )

    if state == "error":
        err_msg = status.get("error", "Unknown error")
        print(color(f"Error: {err_msg}", "red"))


def cmd_start(args: list) -> None:
    fmt = args[0] if args else "35mm"
    result = api_post("/scan/start", {"format": fmt})
    if result:
        print(f"Scan started ({result.get('format', fmt)})")


def cmd_cancel(args: list) -> None:
    result = api_post("/scan/cancel")
    if result:
        print("Cancel requested")


def cmd_status(args: list) -> None:
    status = api_get("/scan/status")
    if status:
        print_status(status)


def cmd_move(args: list) -> None:
    if len(args) < 2:
        print("Usage: move <x_steps> <y_steps>")
        return

    try:
        x = int(args[0])
        y = int(args[1])
    except ValueError:
        print("Error: steps must be integers")
        return

    result = api_post("/motor/move", {"x": x, "y": y})
    if result:
        print(f"Moved: X={result.get('x_steps', x)}, Y={result.get('y_steps', y)}")


def cmd_home(args: list) -> None:
    result = api_post("/motor/home")
    if result:
        print("Motors homed")


def cmd_position(args: list) -> None:
    result = api_get("/motor/position")
    if result:
        x = result.get("x", 0)
        y = result.get("y", 0)
        print(f"Position: X={x}, Y={y}")


def cmd_sethome(args: list) -> None:
    result = api_post("/motor/set_home")
    if result:
        print("Home position set to current location")


def cmd_help(args: list) -> None:
    print("""
Available commands:
  start [format]   Start scan (format: 35mm, 120, 4x5)
  cancel           Cancel running scan
  status           Show scan status
  move <x> <y>     Move motors by steps (x/y can be negative)
  home             Return motors to home
  position         Show motor position (X, Y steps)
  sethome          Set current position as home (calibrate)
  help             Show this help
  quit             Exit CLI
""")


COMMANDS = {
    "start": cmd_start,
    "cancel": cmd_cancel,
    "status": cmd_status,
    "move": cmd_move,
    "home": cmd_home,
    "position": cmd_position,
    "sethome": cmd_sethome,
    "help": cmd_help,
}


def main() -> None:
    print(f"Connected to {API_BASE}")
    print("Scanner CLI — type 'help' for commands")

    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not prompt:
            continue

        parts = prompt.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("quit", "exit", "q"):
            print("Goodbye!")
            break
        elif cmd in COMMANDS:
            COMMANDS[cmd](args)
        else:
            print(f"Unknown command: {cmd}. Type 'help' for available commands.")


if __name__ == "__main__":
    main()
