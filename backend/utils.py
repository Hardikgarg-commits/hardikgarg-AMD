import numpy as np
import torch

from constants import GRID_SIZE, WIND_DRIFT_VECTORS


def clamp_to_bounds(x, y, sector):
    """Clamp (x, y) within sector x-range and the global grid y-range."""
    x = max(sector[0], min(sector[1], x))
    y = max(0, min(GRID_SIZE - 1, y))
    return x, y


def apply_wind_drift(x, y, wind_speed, wind_dir):
    """Apply wind drift physics and return updated (x, y)."""
    drift = wind_speed * 0.02
    dx, dy = WIND_DRIFT_VECTORS.get(wind_dir, (0, 0))
    x += dx * drift
    y += dy * drift
    # AI compensation
    x -= drift * 0.7
    y -= drift * 0.7
    return x, y


def coverage_ratio(heatmap_region):
    """Return the percentage of non-zero cells in a heatmap region."""
    explored = np.count_nonzero(heatmap_region)
    total = heatmap_region.size
    return round((explored / total) * 100, 2)


def get_device_info():
    """Return (device_string, gpu_name) for CUDA/CPU detection."""
    available = torch.cuda.is_available()
    device = "cuda" if available else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if available else "CPU"
    return device, gpu_name


def find_drone(drones, drone_id):
    """Look up a drone by its id. Returns None if not found."""
    for d in drones:
        if d.id == drone_id:
            return d
    return None
