import json
import math

def calculate_zigzag(points, depth=15, deviation=1.0, backstep=3):
    if not points or len(points) < depth:
        return []

    # Deviation is in percentage. So deviation = 1.0 means 1%
    dev_factor = deviation / 100.0

    high_map = {}
    low_map = {}
    
    # Pass 1: Find candidates
    for i in range(len(points)):
        start = max(0, i - depth)
        window = points[start:i+1]
        
        highest_high = max(c["high"] for c in window)
        lowest_low = min(c["low"] for c in window)
        
        if points[i]["high"] == highest_high:
            high_map[i] = highest_high
        
        if points[i]["low"] == lowest_low:
            low_map[i] = lowest_low

    # Pass 2: Filter with backstep and deviation
    # In MT5 zigzag, we keep the absolute highest/lowest in the backstep window
    for i in range(len(points)):
        if i in high_map:
            for j in range(1, backstep + 1):
                if i - j in high_map and high_map[i - j] > high_map[i]:
                    del high_map[i]
                    break
                if i - j in high_map and high_map[i - j] <= high_map[i]:
                    del high_map[i - j]
                    
        if i in low_map:
            for j in range(1, backstep + 1):
                if i - j in low_map and low_map[i - j] < low_map[i]:
                    del low_map[i]
                    break
                if i - j in low_map and low_map[i - j] >= low_map[i]:
                    del low_map[i - j]

    # Pass 3: Connect peaks and valleys enforcing deviation and alternating types
    zigzag_points = []
    
    # Merge and sort by index
    candidates = []
    for i, v in high_map.items():
        candidates.append((i, v, 'high'))
    for i, v in low_map.items():
        candidates.append((i, v, 'low'))
    candidates.sort(key=lambda x: x[0])
    
    if not candidates:
        return []

    last_pivot = None
    
    for idx, val, ptype in candidates:
        if not last_pivot:
            last_pivot = (idx, val, ptype)
            zigzag_points.append(last_pivot)
            continue
            
        last_idx, last_val, last_ptype = last_pivot
        
        if ptype == 'high':
            if last_ptype == 'high':
                if val > last_val:
                    zigzag_points[-1] = (idx, val, ptype)
                    last_pivot = zigzag_points[-1]
            elif last_ptype == 'low':
                if val >= last_val * (1 + dev_factor):
                    last_pivot = (idx, val, ptype)
                    zigzag_points.append(last_pivot)
                    
        elif ptype == 'low':
            if last_ptype == 'low':
                if val < last_val:
                    zigzag_points[-1] = (idx, val, ptype)
                    last_pivot = zigzag_points[-1]
            elif last_ptype == 'high':
                if val <= last_val * (1 - dev_factor):
                    last_pivot = (idx, val, ptype)
                    zigzag_points.append(last_pivot)
                    
    # Format to match chart.js expectation
    return [{"time": points[i]["time"], "value": v} for i, v, _ in zigzag_points]

# Test with a mock sine wave
points = []
for i in range(100):
    val = 100 + 10 * math.sin(i / 5.0)
    points.append({"time": i, "high": val + 2, "low": val - 2, "close": val})

z = calculate_zigzag(points, depth=5, deviation=5.0, backstep=2)
print("Points count:", len(points))
print("ZigZag count:", len(z))
for pt in z:
    print(pt)
