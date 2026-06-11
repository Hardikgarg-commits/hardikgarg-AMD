
import torch
from ultralytics import YOLO
import time

device = "cuda" if torch.cuda.is_available() else "cpu"
model = YOLO("yolov8n.pt")
model.to(device)

def detect_from_frame(frame):
    start = time.time()
    results = model(frame, verbose=False)
    latency = time.time() - start
    fps = 1/latency if latency > 0 else 0
    detected = any(model.names[int(box.cls[0])] == "person"
                   for r in results for box in r.boxes)
    return detected, latency, fps
