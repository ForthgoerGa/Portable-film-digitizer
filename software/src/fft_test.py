import numpy as np
from scipy.fft import fft2, ifft2

def phase_correlation(a, b):
    # a: reference, b: shifted
    # G = (fft(a) * conj(fft(b))) / |...|
    A = fft2(a)
    B = fft2(b)
    R = A * np.conj(B)
    R /= (np.absolute(R) + 1e-15)
    return np.fft.fftshift(np.real(ifft2(R)))

shape = (64, 64)
dx, dy = 5, -3
base = np.random.rand(*shape)
# np.roll shift is (row_shift, col_shift) -> (dy, dx)
shifted = np.roll(base, shift=(dy, dx), axis=(0, 1))

corr = phase_correlation(base, shifted)
y_mid, x_mid = np.array(corr.shape) // 2
idx = np.unravel_index(np.argmax(corr), corr.shape)
peak_dy, peak_dx = idx[0] - y_mid, idx[1] - x_mid

# For b = roll(a, (dy, dx)), the peak in A*conj(B) occurs at (-dy, -dx) 
# if using standard FFT convention where conj(B) negates shift.
# Let's verify.
print(f"True shift:  dx={dx}, dy={dy}")
print(f"Peak offset: dx={peak_dx}, dy={peak_dy}")
print(f"Match logic: dx_match={-peak_dx == dx}, dy_match={-peak_dy == dy}")
