import numpy as np
from scipy.fft import fft2, ifft2

# Setup
H, W = 512, 512
scene = np.random.rand(H, W).astype(np.float32)

dx_true, dy_true = 40, -20
# Tile A at (200, 200), size (128, 128)
ay, ax = 200, 200
th, tw = 128, 128
tileA = scene[ay:ay+th, ax:ax+tw]

# Tile B origin relative to A is (dx_true, dy_true)
by, bx = ay + dy_true, ax + dx_true
tileB = scene[by:by+th, bx:bx+tw]

# Predicted offset
dx_pred, dy_pred = 34, -16

# Overlap relative to A
# Overlap area in A: [max(0, dy_pred):min(th, th+dy_pred), max(0, dx_pred):min(tw, tw+dx_pred)]
# Overlap area in B: [max(0, -dy_pred):min(th, th-dy_pred), max(0, -dx_pred):min(tw, tw-dx_pred)]

ay_start, ay_end = max(0, dy_pred), min(th, th + dy_pred)
ax_start, ax_end = max(0, dx_pred), min(tw, tw + dx_pred)

by_start, by_end = max(0, -dy_pred), min(th, th - dy_pred)
bx_start, bx_end = max(0, -dx_pred), min(tw, tw - dx_pred)

patchA = tileA[ay_start:ay_end, ax_start:ax_end]
patchB = tileB[by_start:by_end, bx_start:bx_end]

# Phase Correlation
def phase_correlation(im1, im2):
    F1 = fft2(im1)
    F2 = fft2(im2)
    R = F1 * np.conj(F2)
    R /= np.absolute(R)
    cross_correlation = np.real(ifft2(R))
    shift = np.unravel_index(np.argmax(cross_correlation), cross_correlation.shape)
    
    # Adjust for wrap-around
    sy, sx = shift
    if sy > cross_correlation.shape[0] // 2: sy -= cross_correlation.shape[0]
    if sx > cross_correlation.shape[1] // 2: sx -= cross_correlation.shape[1]
    return sx, sy

sx, sy = phase_correlation(patchA, patchB)

print(f"Diff (true-pred): {dx_true - dx_pred}, {dy_true - dy_pred}")
print(f"Estimated Shift: {sx}, {sy}")
