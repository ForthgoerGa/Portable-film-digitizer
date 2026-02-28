
# Lab Notebook: Arnav Gaddam, Team 23
## Film Digitizer 

### 2/16 — Introduction

Joined group to work on the film digitizer project. Read through existing proposal and looked into background/theory on analog film and digitization processes. Had a meeting with Gerasimos and the team to discuss project timeline and first steps to get started with the design process. Started to look into Raspberry Pi development by borrowing the group's device and installing a fresh operating system.

### 2/20  — Team Contract and Parts
Worked with group on the team contract and purchase list for necessary parts. Looked into motor assembly and design given to the machine shop. 

### 2/22 — Camera Modules
Evaluated camera options to see what would work with our setup. I already had a standard Raspberry Pi Camera Module(https://www.raspberrypi.com/products/camera-module-v2/), which I brought up to the group. We checked the specifications, and found that it was not suitable for the following reasons:
- It uses a pinhole lens, and isn't designed for high-quality captures
- Uses autofocus without full manual focus capabilities
- Can't add on extra lenses such as the ones we need to achieve the target field of view\
- Only has an 8 megapixel sensor
- Footprint of the module itself is very small (around 25mm x 25mm) making it very susceptible to motion blur if mounted on a motor

As a result, we chose to go with the Raspberry Pi High-Quality camera (https://www.raspberrypi.com/products/raspberry-pi-high-quality-camera/). It has a 12-megapixel Sony IMX477 sensor and support 12-bit RAW output. This is much better for our project because it was designed for high-quality captures, and can mount a specialized lens and extension tube to achieve our desired focal distance of 8.5 cm. 

### 2/27 — Design Document & Raspberry Pi Development
Worked throughout the week on our design document, which was built on top of the proposal. Organized all the components and functionalities into well-defined subsystems with clear input/output and success criteria. Worked with the team to define requirement and verification tables for each subsytem, making sure that satisfying all verifications would leave us with a functional solution. 

In addition to the design document, I also spent the week making sure that the Raspberry Pi was setup for headless access and could automatically connect to a Wi-Fi network on boot. I also configured the SSH server with support for gadget mode, and created Linux user accounts for myself and my teammates. I then started scaffolding the control plane server, looking into a variety of options. At first I considered using a lower-level language like C or C++ to keep code efficient and performant on the resource-constrained Raspberry Pi. However, this had a few limitations:
1) Made it hard to develop locally (since our laptops have a different platform architecture than a Raspberry Pi)
2) C-based code is very prone to segmentation faults or other memory management errors. For code that needs to interact with hardware, this could be dangerous. For example, what happens if the code crashes in the middle of a scan? Now the hardware and software are out of sync.
3) Ease of development. C code is much harder to work with than Python, and it takes more effort to extend with more features. Raspberry Pi also has a strong Python ecosystem, which is useful for us when working with GPIO ports and camera libraries. Those are the components we need the best performance for, so as long as performance is similar the pros outweigh the cons. 

[https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/]

| Requirements | Verification |
| :--- | :--- |
| The serverless compute environment must be able to execute a Python image processing pipeline to perform negative inversion and denoising with sub 30-second latency per image, with a 5-second margin of error. | Make requests to cloud endpoints with raw images and inspect logs to verify that end-to-end latency is below the target threshold. Use a timing library to record end-to-end latency. |
| The server needs to expose an API interface that the Raspberry Pi can use to upload raw camera data and receive the end result. | Perform HTTP Post requests from the Raspberry Pi controller process with sample images. Verify that the service returns a success message upon receipt and a valid URL to the processed output image. |
| The Raspberry Pi needs to synchronize camera captures through libcamera with motion commands to the microcontroller. It needs to ensure that the stage is stationary during a capture to prevent motion blur. The capture will need to be taken 500ms ± 50ms after the last motor pulse is issued. | Use a logic analyzer to monitor the control signals from the Pi and the pulses issued from the microcontroller to the motors. Verify that the delay between the last pulse and the capture signal is large enough to avoid motion blur. |
| The STM32 needs to interpret serial commands from the Raspberry Pi and generate motor and LED output signals with low latency. The STM32 must issue a motor pulse within 10ms ± 2ms of receiving a completed control command. | Manually issue movement commands through a serial console. Use an oscilloscope to measure the end-to-end transmission time and evaluate against the threshold. |
| The image stitching algorithm on the Raspberry Pi needs to successfully combine multiple overlapping frames into a single 4K (3840x2160) image with no visible discontinuities or alignment errors ( > 5 pixels ± 1 pixel). | Run the capture sequence on a test image (perhaps a printed grid). Inspect the stitched output to visually verify that there are no alignment issues. Use a calibrated ISO 12233 resolution chart and measure pixel offset at stitch boundaries using an image editing tool. |
