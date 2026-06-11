import logging
import time

import torch
from ultralytics import YOLO

logger = logging.getLogger("detection")

device = "cuda" if torch.cuda.is_available() else "cpu"

try:
    model = YOLO("yolov8n.pt")
    model.to(device)
except Exception:
    logger.exception("Failed to load YOLO model from yolov8n.pt")
    raise


def detect_from_frame(frame):
    """Run person-detection on *frame* and return (detected, latency, fps).

    Returns ``(False, 0.0, 0.0)`` when the frame is ``None`` or inference
    fails, so callers always receive a usable tuple.
    """
    if frame is None:
        logger.warning("detect_from_frame called with None frame")
        return False, 0.0, 0.0

    try:
        start = time.time()
        results = model(frame, verbose=False)
        latency = time.time() - start
        fps = 1 / latency if latency > 0 else 0
        detected = any(
            model.names[int(box.cls[0])] == "person"
            for r in results
            for box in r.boxes
        )
        return detected, latency, fps
    except Exception:
        logger.exception("Inference error in detect_from_frame")
        return False, 0.0, 0.0
