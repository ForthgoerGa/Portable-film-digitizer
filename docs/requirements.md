# Requirements and Verification

## Functional Requirements
1. Capture and process at least three formats: 35mm, 120, Instax Mini.
2. Produce output image at or above 3840x2160.
3. Complete capture-to-delivery under 15 seconds on stable Wi-Fi.
4. Run from battery power without external adapter during use.
5. Provide user feedback for idle/capture/upload/success/error states.

## Physical Requirements
1. Overall size under 30 cm x 20 cm x 15 cm.
2. Mass under 1.5 kg.
3. Film carrier insertion must be tool-free.

## Power Requirements
1. Support at least 360 captures on one full charge.
2. Maintain stable illumination intensity during low battery.

## Verification Plan
- Resolution: test chart capture and measured pixel dimensions.
- Dynamic range: evaluate shadow/highlight retention on Portra 400 negative sample.
- Latency: timestamp each stage over at least 30 trials.
- Versatility: run end-to-end workflow for all three formats.
- Battery: execute continuous capture run to count total completed shots.
