# -*- coding: utf-8 -*-
import cv2
import numpy as np
import logging

logger = logging.getLogger("Motion")

class MotionDetector:
    """Handles motion detection across multiple camera streams."""
    def __init__(self, threshold=5000):
        self.threshold = threshold
        self.prev_gray_frames = {}

    def update_threshold(self, threshold):
        """Allows dynamically updating the motion threshold from config."""
        self.threshold = threshold

    def detect_motion(self, camera_frames_dict):
        """
        Detects motion across a dictionary of camera frames: {camera_index: raw_frame}.
        Applies Gaussian blur to raw frames to eliminate noise and artifacts before comparison.
        Returns True if motion is detected, False otherwise.
        """
        if not camera_frames_dict:
            return False

        total_diff = 0
        motion_detected = False

        for cam_idx, frame in camera_frames_dict.items():
            if frame is None:
                continue

            try:
                # Convert to grayscale and apply Gaussian blur to eliminate sensor noise
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                if cam_idx in self.prev_gray_frames:
                    prev_gray = self.prev_gray_frames[cam_idx]
                    
                    # Ensure frames have matching sizes
                    if prev_gray.shape == gray.shape:
                        diff = cv2.absdiff(prev_gray, gray)
                        # We can apply a threshold to get clear motion contours, but to match
                        # the legacy sum behavior we can sum the differences of thresholded pixels
                        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                        total_diff += np.sum(thresh)
                
                # Update history
                self.prev_gray_frames[cam_idx] = gray
            except Exception as e:
                logger.error("Error during motion detection on camera %s: %s", cam_idx, e)

        # If total accumulated changes exceed the configuration threshold, motion is detected
        if total_diff > self.threshold:
            logger.debug("Motion detected! Total diff: %d, Threshold: %d", total_diff, self.threshold)
            motion_detected = True

        return motion_detected

    def reset_history(self):
        """Resets the history of previous frames."""
        self.prev_gray_frames.clear()
