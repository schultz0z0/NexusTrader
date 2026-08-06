def calculate_zigzag(points, depth=15, deviation=1.0, backstep=3):
    if not points or len(points) < depth:
        return []

    dev_factor = deviation / 100.0

    high_map = {}
    low_map = {}
    
    # Pass 1: Encontrar os topos e fundos absolutos no periodo Depth
    for i in range(len(points)):
        start = max(0, i - depth + 1)
        window = points[start:i+1]
        
        highest_high = max(c.get("high", c.get("value", c.get("close", 0))) for c in window)
        lowest_low = min(c.get("low", c.get("value", c.get("close", 0))) for c in window)
        
        if points[i].get("high", points[i].get("value", points[i].get("close", 0))) == highest_high:
            high_map[i] = highest_high
        
        if points[i].get("low", points[i].get("value", points[i].get("close", 0))) == lowest_low:
            low_map[i] = lowest_low

    # Pass 2: Filtro Backstep (remover topos/fundos muito proximos)
    for i in range(len(points)):
        if i in high_map:
            for j in range(1, backstep + 1):
                if i - j in high_map:
                    if high_map[i - j] > high_map[i]:
                        del high_map[i]
                        break
                    else:
                        del high_map[i - j]
                        
        if i in low_map:
            for j in range(1, backstep + 1):
                if i - j in low_map:
                    if low_map[i - j] < low_map[i]:
                        del low_map[i]
                        break
                    else:
                        del low_map[i - j]

    # Pass 3: Alternancia e Filtro Deviation (confirmacao do pivot)
    candidates = []
    for i, v in high_map.items():
        candidates.append((i, v, 'high'))
    for i, v in low_map.items():
        candidates.append((i, v, 'low'))
    candidates.sort(key=lambda x: x[0])
    
    if not candidates:
        return []

    zigzag_points = []
    last_pivot = candidates[0]
    zigzag_points.append(last_pivot)
    
    for idx, val, ptype in candidates[1:]:
        last_idx, last_val, last_ptype = last_pivot
        
        if ptype == 'high':
            if last_ptype == 'high':
                if val >= last_val:
                    zigzag_points[-1] = (idx, val, ptype)
                    last_pivot = zigzag_points[-1]
            elif last_ptype == 'low':
                if val >= last_val * (1 + dev_factor):
                    last_pivot = (idx, val, ptype)
                    zigzag_points.append(last_pivot)
                    
        elif ptype == 'low':
            if last_ptype == 'low':
                if val <= last_val:
                    zigzag_points[-1] = (idx, val, ptype)
                    last_pivot = zigzag_points[-1]
            elif last_ptype == 'high':
                if val <= last_val * (1 - dev_factor):
                    last_pivot = (idx, val, ptype)
                    zigzag_points.append(last_pivot)
                    
    # Perna flutuante: Adiciona a perna em andamento (ate a vela mais atual) 
    # para que o grafico seja dinamico e exiba o "repintura" no ultimo toque
    if zigzag_points:
        last_idx, last_val, last_ptype = zigzag_points[-1]
        
        if last_idx < len(points) - 1:
            if last_ptype == 'high':
                extreme_val = min(c.get("low", c.get("value", c.get("close", 0))) for c in points[last_idx + 1:])
                extreme_idx = next(i for i in range(last_idx + 1, len(points)) if points[i].get("low", points[i].get("value", points[i].get("close", 0))) == extreme_val)
                zigzag_points.append((extreme_idx, extreme_val, 'low'))
            else:
                extreme_val = max(c.get("high", c.get("value", c.get("close", 0))) for c in points[last_idx + 1:])
                extreme_idx = next(i for i in range(last_idx + 1, len(points)) if points[i].get("high", points[i].get("value", points[i].get("close", 0))) == extreme_val)
                zigzag_points.append((extreme_idx, extreme_val, 'high'))

    return [{"time": points[i]["time"], "value": v, "type": ptype} for i, v, ptype in zigzag_points]
