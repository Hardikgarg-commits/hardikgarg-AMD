"""Bounding-box geometry helpers used for spatial association of detections."""

from __future__ import annotations

BBox = tuple[float, float, float, float]


def normalize_bbox(bbox: list[float] | tuple[float, ...]) -> BBox:
    """Return the bbox as an ``(x_min, y_min, x_max, y_max)`` tuple."""
    x_min, y_min, x_max, y_max = (float(v) for v in bbox)
    return (min(x_min, x_max), min(y_min, y_max), max(x_min, x_max), max(y_min, y_max))


def area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def iou(a: BBox, b: BBox) -> float:
    """Intersection over union of two axis-aligned boxes."""
    inter_x_min = max(a[0], b[0])
    inter_y_min = max(a[1], b[1])
    inter_x_max = min(a[2], b[2])
    inter_y_max = min(a[3], b[3])
    inter = max(0.0, inter_x_max - inter_x_min) * max(0.0, inter_y_max - inter_y_min)
    if inter == 0.0:
        return 0.0
    union = area(a) + area(b) - inter
    if union <= 0.0:
        return 0.0
    return inter / union


def merge_bbox(a: BBox, weight_a: float, b: BBox, weight_b: float) -> BBox:
    """Confidence-weighted average of two boxes (used for track representatives)."""
    total = weight_a + weight_b
    if total <= 0:
        return b
    return (
        (a[0] * weight_a + b[0] * weight_b) / total,
        (a[1] * weight_a + b[1] * weight_b) / total,
        (a[2] * weight_a + b[2] * weight_b) / total,
        (a[3] * weight_a + b[3] * weight_b) / total,
    )
