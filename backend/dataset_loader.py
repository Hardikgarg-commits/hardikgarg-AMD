
import os
import cv2

class DatasetLoader:
    def __init__(self, path="../dataset"):
        self.images = []
        if os.path.exists(path):
            for file in os.listdir(path):
                if file.lower().endswith((".jpg",".png")):
                    self.images.append(cv2.imread(os.path.join(path,file)))
        self.index = 0

    def get_next_frame(self):
        if not self.images:
            return None
        frame = self.images[self.index]
        self.index = (self.index + 1) % len(self.images)
        return frame
