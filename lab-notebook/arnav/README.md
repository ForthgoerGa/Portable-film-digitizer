
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

### 3/5 — Design Review Feedback
Had our design review with Gerasimos and Professor Gruev. They gave us positive feedback on our design, but also proposed certain improvements for technical specifications, concrete requirements, and plans to include advanced capabilities. One specific piece of feedback from Professor Gruev was to re-evaluate our maximum allowed alignment error of 5 pixels, as this may be too large to properly reconstruct an image. We noted this down as a future work item. 

### 3/8 — Software Development Meeting
Worked with the team to set up development environment and basic architecture for the software stack. We evaluated a few different approaches, but we decided to split the main responsibility between the Raspberry Pi and STM32 microcontroller. The Raspberry Pi will have a stateful, persistent API server that keeps track of physical hardware state. For instance, it would keep track of motor position and scan lifecycle. Meanwhile, the actual motor control loop would be handled on the STM32. This keeps the Raspberry Pi server fast and not weighed down by extremely frequent GPIO pulses, keeping motors smooth and stable even under heavy image processing work. 

Resolution: 4056 x 3040 pixels  
Bit Depth: 12-bit RAW  

$$\frac{Total\ Pixels \times Bits/Pixel}{8} = 18.5 \text{ MB}$$

Stotal — data transfer size  
Sraw — size of single capture  
Sproc — size of processed output  
N — number of frames  
BW — network speed in Mbps  

$$S_{total} = (N \times S_{Raw}) + S_{Proc} = 320.9 \text{ MB}$$

$$T_{network} = \frac{S_{total} \times 8}{BW} = 256.72 \text{ seconds}$$

We used these calculations when deciding how much processing to perform on the cloud as opposed to on-device with the Raspberry Pi. If we had to complete image stitching in the cloud, it would require transferring each of the 16 raw captures over the network. With a stable bandwidth of about 10 mbps, that would take over 4 minutes due to the data size. The RAW captures have a lot of unecessary information; if we can do processing locally on the pi such as stitching to reduce image size before transferring, then we can greatly reduce end to end latency.

### 3/9 — TA Meeting & Control Plane Development

We met with Gerasimos to show him the progress on the software side of the project as we waited for components to arrive. We built a simple user interface to start a scan and monitor it's progress, with the actual motor functionality stubbed out. For example, when our code called move(X, Y), we simply logged to console instead of issuing a command to the STM32 over UART as we would in production. Leo found mock images of negative film online, which he preprocessed and split into several frame captures. This allowed us to keep refining the control plane UI / state tracking and image processing pipeline while waiting for components to arrive. 

I evaluated a couple of options like Python’s Flask and Django frameworks, but settled on using FastAPI alongside Uvicorn to create a HTTP web server as simply as possible for our use case. In production, this would be run as a system service so that it is resilient across failures or crashes since the user won’t be able to manage the software themself. 

This following snippet represents how I setup a server to render static files (HTML/CSS for the user interface) as well as API routes to start a scan and retrieve device status. 
```python
@app.get("/")
def root():
    return RedirectResponse(url="/web/")


@app.post("/scan")
def start_scan(req: dict):
    film_format = req.get("format", "35mm")
    try:
        coordinator.start_job(film_format)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "started", "format": film_format}


@app.get("/status")
def get_status():
    return coordinator.status_dict()
```
[Insert picture of control plane UI]


### 3/20 — AWS Lambda Integration

To handle the more intensive parts of capture post-processing (after the stitching process) I looked into using a cloud provider such as Amazon Web Services for additional compute resources. I evaluated several tools including virtual private servers (Amazon EC2) and serverless compute (AWS Lambda). Since we are working on an edge device that can only physically support one scan at a time, a lot of usual considerations that go into choosing a distributed/cloud solution weren’t applicable. Namely, we don’t have a need for high throughput performance under scale. Instead, we want to minimize latency so that users aren’t left waiting in the real world as their scan completes. Our project also inherently has bursty traffic, since an interactive device is not going to always be making requests or performing work. Users might use our device for maybe an hour on a single day of the week/month as they need to work on digitizing their physical film. Using an always-running virtual machine in the cloud would be overkill, since we cannot predict when a user will be using our device and as such must keep the server running at all times. 

The solution I found for this was to use AWS Lambda. Functionally, serverless is similar to a traditional server. We issue some request to the cloud, and it performs some work and returns a response. However, serverless has the advantage of being on-demand, meaning that the cloud provider (AWS in this case) manages a pool of virtual machines that can be woken up as needed to serve our requests. This means that we don’t need to manage actual servers, which would add even more complexity to our project and shift the focus away from a hardware-based solution. However, this convenience comes at a performance cost, since we now have to incur some initial latency for requests as we wait for Amazon to spin up a virtual machine to handle our requests. 

I worked with Gemini to create the following latency benchmarking script to make sure that latency is not too high:
```python
import boto3
import time
import json
import statistics

# Initialize the Lambda client
lambda_client = boto3.client('lambda', region_name='us-east-1')

FUNCTION_NAME = 'FilmDigitizer_Inversion_Pipeline'

def trigger_lambda(invocation_type='RequestResponse'):
    """
    Invokes the Lambda function and returns the end-to-end 
    latency from the client's perspective.
    """
    start_time = time.perf_content_cur_ns()
    
    response = lambda_client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType=invocation_type,
        Payload=json.dumps({"test": "latency_run"})
    )
    
    # Read the response to ensure the round-trip is complete
    response['Payload'].read()
    
    end_time = time.perf_content_cur_ns()
    
    # Convert nanoseconds to milliseconds
    return (end_time - start_time) / 1_000_000

def run_experiment():
    print(f"--- Starting Latency Experiment for {FUNCTION_NAME} ---")
    
    # 1. Cold Start Measurement
    # Note: This assumes the function hasn't been called in ~30 mins
    print("Measuring Cold Start...")
    cold_latency = trigger_lambda()
    print(f"Cold Start Latency: {cold_latency:.2f} ms")
    
    # 2. Warm Start Measurements
    # We run multiple iterations to get an average 'Warm' state
    warm_latencies = []
    print("\nMeasuring Warm Starts (5 iterations)...")
    for i in range(5):
        time.sleep(2) 
        latency = trigger_lambda()
        warm_latencies.append(latency)
        print(f"Iteration {i+1}: {latency:.2f} ms")
    
    avg_warm = statistics.mean(warm_latencies)
    
    print("\n--- Final Results ---")
    print(f"Total Cold Start Overhead: {cold_latency:.2f} ms")
    print(f"Average Warm Start Latency: {avg_warm:.2f} ms")
    print(f"Calculated Cold Start Penalty: {cold_latency - avg_warm:.2f} ms")

if __name__ == "__main__":
    run_experiment()
```

[AWS Cloudwatch latency trace]

Even worst-case latency in the case of a cold start is amortized by the overall runtime of our scan, so it is not a concern. 

Procedure

1) Cold Start: The function is invoked after a redeployment to make sure that AWS has to provision a new VM to handle the request.
2) Warm Start: The function is invoked immediately (within 10 seconds) after a successful execution to reuse the "frozen" container.
3) Measurement: We utilized AWS CloudWatch to parse the init duration and billed duration metrics.

Results

| Metric | Cold Start (ms) | Warm Start (ms) | Variance |
| :--- | :--- | :--- | :--- |
| Initialization (Init) | 2,450 ms | 0 ms | -100% |
| Image Inversion Script | 1,120 ms | 1,080 ms | -3.5% |
| Total Overhead | 3,570 ms | 1,080 ms | -69.7% |


### 3/27 — TA Meeting, PCB  Verification
Met with Gerasimos this week and discussed previous work on the software architecture as well as next steps to assemble the circuit on a breadboard and PCB now that parts have arrived. Me and Guyan planned to work together over the next few weeks to incrementally solder components on and test in isolation so that we could create a functional prototype. 

### 4/1 — Individual Progress Report
I worked on my individual progress report by looking back on my contributions to the project and thinking about future work. 

### 4/5 — Voltage Regulation and Component Testing

We currently face the challenge of being able to verify our motion subsystem without having the rest of the subsystems implemented (i.e control, power, etc). As a result, I came up with a method to independently test the motor and motor drivers without relying on the rest of the components to be successfully soldered. 

Method: Use a bench power supply to directly connect to our PCB power rail in place of the actual voltage regulator and battery unit. Connect both 4-pin motor connectors to the drivers on the board, ensuring that necessary capacitors and resistors are also installed. Finally, connect the I/O pins of an STM32 dev board to the underside I/O pins on the motor drivers. This way, we can program our STM32 dev board to control the motors through the motor drivers without having a functional power subsystem or soldered-on microcontroller. 

Dstep — Linear displacement per microstep  
P — Lead screw pitch (mm/rev) = 2 mm  
Srev — Full steps per motor revolution = 200 steps  
M — Microstepping factor = 16  
V — Linear velocity (mm/s)  
f — Step frequency (Hz)  
Npulses — Total number of pulses issued  
ΔL — Target displacement (mm)  

$$D_{step} = \frac{P}{S_{rev} \times M} = \frac{2}{3200} \text{ mm/step}$$

$$V = f \times D_{step}$$

$$N_{pulses} = \frac{\Delta L}{D_{step}}$$

Using the equations above, we can modify the constants in our code to achieve any desired velocity and displacement, within physical reason. Future work remains to tune acceleration/deceleration profiles since we want to minimize missed steps, as we have an open-loop motor design.

### 4/13 — Solder STM32 
I attempted to solder the STM32 onto our PCB using several different methods since we didn't have a stencil with our order. At first, I tried to use solder paste with a heat gun to heat around the edges of the chip footprint. This did not work though, as the solder paste towards the center of the chip did not get hot enough. Since all the solder points were between the PCB and the chip, I also had no way to verify if connections were solid. The next thing I tried after consulting the E-Shop was to use a soldering iron, and try drag-soldering small beads of solder across all 50+ pins on the underside of the chip. I was able to achieve a satisfactory result with this, after checking for any shorts or missing solder with the X-Ray machine in the E-Shop. This was a time consuming step and took a few days of effort. However, we switched focus from trying to get the MCU working on the PCB because we were running short on time and didn't yet have a functional prototype. 

[Insert image of soldered STM32]

### 4/18 — STM32 Motor Controller and UART Listener

One other part I was working on is the communication protocol between the Raspberry Pi and STM32 microcontroller. Our design relies on this architecture to achieve low-latency motor control while also having the processing power and memory resources necessary to take high-quality camera captures. While thinking about the boundary between the two devices, I found that a suitable approach would be to have the controlling device issue absolute coordinates to the microcontroller in the form of (X, Y) coordinates within the stage, which the latter would then transform into motor driver signals. This way all the motor computation can be kept as close to the hardware as possible, and our Raspberry Pi won’t be slowed down by having to manage the high-frequency motor control signals. 

I configured our STM32 with the appropriate GPIO pins for STEP_X, DIR_X, STEP_Y, and DIR_Y. I also configured the TX/RX pins to be used as a USART interface, allowing for serial communication between itself and a master device. To make this UART control interface efficient I used interrupts instead of polling, so that the microcontroller did not waste valuable CPU cycles that could be used for motor and LED control. 

Test Setup
- Hardware: Connect the Raspberry Pi UART TX to the STM32 UART RX and rpi RX to STM32 TX
- Measurement: Attach Channel 1 of a logic analyzer to the UART TX line and Channel 2 to the STM32 step output pin leading to the A4988 driver
- Trigger: Use a Python script on the Pi to send a standardized motion packet (ex. MOVE_X:100, MOVE_Y:100)

Procedure
1. Initialize the Raspberry Pi serial port at 115,200 baudrate
2. Trigger the logic analyzer to record on the falling edge of the UART start bit
3. The STM32 firmware needs to parse the incoming string using an interrupt-based circular buffer to minimize CPU overhead
4. Once the newline character (\n) is detected, the STM32 toggles the step pin for the NEMA 17 motor

```c
// Constants and Buffers
#define BUFFER_SIZE 64
char rx_buffer[BUFFER_SIZE]
int rx_index = 0
bool command_ready = false

// --- UART RX Interrupt Handler ---
void UART_RX_Interrupt_Handler():
    char received_char = READ_UART_DATA_REGISTER()
    
    // Store in circular buffer
    if (received_char != '\n' && rx_index < BUFFER_SIZE - 1):
        rx_buffer[rx_index] = received_char
        rx_index++
    else:
        // Newline detected: terminate string and signal the main loop
        rx_buffer[rx_index] = '\0'
        command_ready = true
        DISABLE_UART_RX_INTERRUPT() // Pause RX until current command is handled

// --- Main Loop ---
void main():
    INIT_GPIO_STEP_DIR_PINS()
    INIT_UART_INTERRUPTS(115200)
    
    while (true):
        if (command_ready):
            // 1. Parse Packet (Ex: "MOVE_X:100")
            int steps_x = 0, steps_y = 0
            PARSE_COMMAND(rx_buffer, &steps_x, &steps_y)
            
            // 2. Set Direction Pins
            SET_DIR_X(steps_x > 0 ? HIGH : LOW)
            SET_DIR_Y(steps_y > 0 ? HIGH : LOW)
            
            // 3. Execute Motion (Square wave toggle)
            for (int i = 0; i < ABS(steps_x); i++):
                SET_STEP_X_PIN(HIGH)
                DELAY_MICROSECONDS(500)
                SET_STEP_X_PIN(LOW)
                DELAY_MICROSECONDS(500)
                
            // 4. Reset for next packet
            rx_index = 0
            command_ready = false
            ENABLE_UART_RX_INTERRUPT()
```
Conceptual design for STM32 motor control interface. Attribution: Google Gemini 3

One design decision I made was to make the serial commands synchronous instead of asynchronous. This means that when the Pi issues a command, a response is only received over UART when the STM finishes issuing the computed number of pulses. This makes sure that the Raspberry Pi scan coordinator does not prematurely try to issue new movement commands while previous ones are still running, as this could cause inconsistencies in state.

### 4/22 — Work on End-to-End Scan Coordination
After we had all of our pieces working individually, we worked on integrating everything into a "one-click" scan. This means that we should be able to open to insert a piece of film, click "start scan" and watch as the camera and film stage move around to capture the necessary frames. After the physical captures, post processing should be invoked automatically and return the output image. One thing that gave us a lot of trouble in this phase was unstable motor current; when using our battery, we sometimes saw that even when no motors were moving on their own even without pulses issued. Allan helped diagnose the issue by finding out that when using the battery the circuit suffered from drops in voltage to the motor driver, which meant that instead of the normal 3.3V logic level it was operating much lower. That meant that even small noise (such as a 50mV ripple) was being treated as a HIGH signal. 

Another major issue was calibration. Even though motor position did not drift at all within a single run, missed steps accumulated over time and cause noticeable errors in image quality. We solved this for the time being with a calibration mode, which allowed us to manually move motors to their origin positions and then mark their state. However, this requires manual intervention and is not sustainable for production. A major improvement we could make for our design is to add rotary encoders to our motors, so that we can have a feedback loop for self-correction using PID control or something similar. 

### 4/24 — Investigate Mechanical Issue
The week before our demo, our motors started slipping and jerking while turning. I attempted to debug this by monitoring the STEP signals output by the microcontroller, but saw that there was no issue there. This led me to try and diagnose the motor current, but I saw that it was adequate as well. After more troubleshooting, I found that one of the motors was facing a mechanical issue that prevented it from turning. We worked with Paul from the machine shop to tune our assembly and loosen some screws, and then the problem was solved. 

### 4/25 — Attempt PCB Integration and Troubleshooting
After we achieved full functionality on the breadboard, Allan and I attempted to move our design onto the PCB. Although we were not able to get the microcontroller to work on the PCB, we theorized that the rest of the circuit could be transitioned over, which would reduce the number of wires and loose components inside our assembly. However, we faced an issue where one of the motor driver sockets on the PCB was not receiving the correct voltage. 

### 4/26 — Final Changes to Form Factor, Organization, and Robustness
Worked with the group to refine the software interface and physical organization of components within the assembly since we were not able to get our PCB to work. I organized motor cables, wires used for control unit signals, and overall robustness of the system to development changes like plugging in wires, attaching battery, and so on.

### 4/27 — Final Demo
Demonstrated our project to Gerasimos and Professor Gruev. We lost points for not using our PCB, but were able to achieve full functionality on the rest of our design. 

### 4/28 — Work on Final Presentation
Worked with the group to design slides and graphics for the final presentation. Practiced presentation, cut down overly wordy content, and made sure all our findings and verifications were present. 

### 4/30 — Final Presentation 
Presented to Gerasimos and Professor Gruev. We were asked questions about open-loop motor design, voltage ripples from capacitors, as well as image processing quality.
Following the presentation, I worked on editing our demo video before uploading to the web board. 
