
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

