import os
import glob
import re
import numpy as np
from PIL import Image

def get_tiles():
    files = glob.glob("captures/row_*_col_*.jpg")
    tiles = {}
    for f in files:
        match = re.search(r'row_(\d+)_col_(\d+)\.jpg', f)
        if match:
            r, c = int(match.group(1)), int(match.group(2))
            tiles[(r, c)] = f
    return tiles

def estimate_shift(img_a_path, img_b_path, expected_shift):
    # Load images as grayscale
    img_a = np.array(Image.open(img_a_path).convert('L'), dtype=np.float32)
    img_b = np.array(Image.open(img_b_path).convert('L'), dtype=np.float32)
    
    # We want to find displacement (dx, dy) such that B[y, x] matches A[y-dy, x-dx]
    # Or B is at (dx, dy) relative to A.
    # Simple cross-correlation in a window around expected shift
    ex, ey = expected_shift
    win = 100 # search window +/- win
    
    # Crop a central patch from img_b to use as template
    h, w = img_b.shape
    patch_size = 500
    p_y0, p_x0 = h // 2, w // 2
    patch = img_b[p_y0-patch_size//2 : p_y0+patch_size//2, 
                  p_x0-patch_size//2 : p_x0+patch_size//2]
    
    # Normalize patch
    patch = (patch - np.mean(patch)) / (np.std(patch) + 1e-5)
    
    best_corr = -1
    best_shift = (ex, ey)
    
    for dy in range(ey - win, ey + win + 1, 2):
        for dx in range(ex - win, ex + win + 1, 2):
            # Target region in A should correspond to patch in B if B is at (dx, dy)
            # A_coord = B_coord - shift
            ay0, ax0 = p_y0 - dy - patch_size // 2, p_x0 - dx - patch_size // 2
            if ay0 < 0 or ax0 < 0 or ay0 + patch_size > h or ax0 + patch_size > w:
                continue
            
            target = img_a[ay0 : ay0 + patch_size, ax0 : ax0 + patch_size]
            target = (target - np.mean(target)) / (np.std(target) + 1e-5)
            
            corr = np.sum(patch * target)
            if corr > best_corr:
                best_corr = corr
                best_shift = (dx, dy)
                
    # Refine
    rx, ry = best_shift
    for dy in range(ry - 2, ry + 3):
        for dx in range(rx - 2, rx + 3):
            ay0, ax0 = p_y0 - dy - patch_size // 2, p_x0 - dx - patch_size // 2
            if ay0 < 0 or ax0 < 0 or ay0 + patch_size > h or ax0 + patch_size > w:
                continue
            target = img_a[ay0 : ay0 + patch_size, ax0 : ax0 + patch_size]
            target = (target - np.mean(target)) / (np.std(target) + 1e-5)
            corr = np.sum(patch * target)
            if corr > best_corr:
                best_corr = corr
                best_shift = (dx, dy)
                
    return best_shift

def main():
    tiles = get_tiles()
    rows = max(r for r, c in tiles.keys()) + 1
    cols = max(c for r, c in tiles.keys()) + 1
    
    conf_x = (922, 26)
    conf_y = (-12, 1202)
    
    x_shifts = []
    y_shifts = []
    
    print("Horizontal Pairs (Col+1):")
    for r in range(rows):
        for c in range(cols - 1):
            if (r, c) in tiles and (r, c+1) in tiles:
                shift = estimate_shift(tiles[(r, c)], tiles[(r, c+1)], conf_x)
                x_shifts.append(shift)
                print(f"  ({r},{c})->({r},{c+1}): {shift}")
                
    print("\nVertical Pairs (Row+1):")
    for r in range(rows - 1):
        for c in range(cols):
            if (r, c) in tiles and (r+1, c) in tiles:
                shift = estimate_shift(tiles[(r, c)], tiles[(r+1, c)], conf_y)
                y_shifts.append(shift)
                print(f"  ({r},{c})->({r+1},{c}): {shift}")
                
    if x_shifts:
        med_x = np.median(x_shifts, axis=0)
        print(f"\nMedian X-step: {med_x}")
        print(f"Config X-step: {conf_x}")
        print(f"Delta: {med_x - conf_x}")
        
    if y_shifts:
        med_y = np.median(y_shifts, axis=0)
        print(f"\nMedian Y-step: {med_y}")
        print(f"Config Y-step: {conf_y}")
        print(f"Delta: {med_y - conf_y}")

if __name__ == "__main__":
    main()
