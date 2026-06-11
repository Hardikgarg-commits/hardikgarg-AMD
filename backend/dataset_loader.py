import logging
import os

import cv2

logger = logging.getLogger("dataset_loader")


class DatasetLoader:
    def __init__(self, path="../dataset"):
        self.images = []
        if not os.path.exists(path):
            logger.warning("Dataset path does not exist: %s", path)
            return
        for file in os.listdir(path):
            if not file.lower().endswith((".jpg", ".png")):
                continue
            full_path = os.path.join(path, file)
            img = cv2.imread(full_path)
            if img is None:
                logger.warning("Failed to load image (corrupt or unreadable): %s", full_path)
                continue
            self.images.append(img)
        logger.info("Loaded %d images from %s", len(self.images), path)
        self.index = 0

    def get_next_frame(self):
        if not self.images:
            logger.debug("get_next_frame called but no images are loaded")
            return None
        frame = self.images[self.index]
        self.index = (self.index + 1) % len(self.images)
        return frame
