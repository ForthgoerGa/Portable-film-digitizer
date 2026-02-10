# System Architecture

## Block-Level Flow
1. User inserts film carrier and selects mode (or auto-detect).
2. STM32 configures illumination profile and sensor capture parameters.
3. Sensor captures raw frame.
4. Wi-Fi module uploads frame + metadata to cloud endpoint.
5. Cloud function processes image and returns result URL.
6. Device/app displays completion status.

## Hardware Subsystems
- Power subsystem: Li-Ion battery, charger/protection, logic and LED rails.
- Control subsystem: STM32, UI inputs, OLED status output.
- Imaging subsystem: CMOS sensor and lens stack.
- Illumination subsystem: transmissive + reflective arrays, PWM-driven.
- Networking subsystem: Wi-Fi module interface.

## Software Subsystems
- Embedded firmware state machine for capture orchestration.
- Cloud API receiver and storage.
- Image processing pipeline (inversion, denoise, grade).
- Optional mobile/web client for preview and download.
