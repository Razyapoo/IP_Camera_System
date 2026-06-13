# -*- coding: utf-8 -*-
import cv2
import numpy as np
import platform
import subprocess
import logging
import math
import time

logger = logging.getLogger("UI")

def get_screen_resolution():
    """Detects native screen resolution depending on the OS platform."""
    system = platform.system()
    if system == 'Linux':
        try:
            output = subprocess.check_output(['xrandr']).decode()
            for line in output.split('\n'):
                if '*' in line:
                    parts = line.split()
                    for part in parts:
                        if 'x' in part and part.replace('x', '').replace('.', '').isdigit():
                            res = part.split('x')
                            if len(res) == 2:
                                return int(res[0]), int(res[1].split('+')[0])
            output = subprocess.check_output(['xdpyinfo']).decode()
            for line in output.split('\n'):
                if 'dimensions:' in line:
                    parts = line.split()
                    res = parts[1].split('x')
                    return int(res[0]), int(res[1])
        except Exception as e:
            logger.debug("Linux resolution detection failed: %s", e)
    elif system == 'Windows':
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception as e:
            logger.debug("Windows resolution detection failed: %s", e)
    elif system == 'Darwin':
        try:
            output = subprocess.check_output(['system_profiler', 'SPDisplaysDataType']).decode()
            for line in output.split('\n'):
                if 'Resolution:' in line:
                    parts = line.split(':')[1].strip().split(' x ')
                    return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.debug("Darwin resolution detection failed: %s", e)
            
    # Fallback using tkinter
    try:
        import tkinter as tk
        root = tk.Tk()
        width = root.winfo_screenwidth()
        height = root.winfo_screenheight()
        root.destroy()
        return width, height
    except Exception as e:
        logger.debug("Tkinter resolution detection failed: %s", e)
        
    return 1920, 1080

class AppUI:
    """Manages OpenCV windows, layouts, rendering screens, and user input callbacks."""
    def __init__(self, config_manager, on_double_click_cam_callback, on_double_click_close_callback):
        self.config_manager = config_manager
        self.on_double_click_cam_callback = on_double_click_cam_callback
        self.on_double_click_close_callback = on_double_click_close_callback
        
        self.screen_w, self.screen_h = get_screen_resolution()
        logger.info("Screen resolution detected: %dx%d", self.screen_w, self.screen_h)

        self.window_title = self.config_manager.window_title
        self.fullscreen = self.config_manager.fullscreen
        
        # Double click state management
        self.last_click_time = 0
        self.last_click_pos = (0, 0)
        self.DOUBLE_CLICK_DELAY = 0.5
        self.DOUBLE_CLICK_RADIUS = 20

    def init_window(self):
        """Initializes the main OpenCV window and hooks mouse events."""
        cv2.namedWindow(self.window_title, cv2.WINDOW_NORMAL)
        if self.fullscreen:
            cv2.setWindowProperty(self.window_title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        cv2.setMouseCallback(self.window_title, self._mouse_callback)

    def destroy_window(self, title=None):
        """Safely destroys OpenCV window."""
        target_title = title if title is not None else self.window_title
        try:
            cv2.destroyWindow(target_title)
        except Exception:
            pass

    def show_frame(self, frame, title=None):
        """Renders a frame inside the designated OpenCV window."""
        target_title = title if title is not None else self.window_title
        cv2.imshow(target_title, frame)

    def show_startup_screen(self, progress, total, status_text):
        """Renders the startup loading screen with progress bar directly to OpenCV window."""
        img = np.zeros((self.screen_h, self.screen_w, 3), dtype=np.uint8)

        title = "ZAGRUZKA SISTEMY..."
        cv2.putText(img, title, (self.screen_w//2 - 250, self.screen_h//2 - 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3, cv2.LINE_AA)

        bar_w = 600
        bar_h = 40
        bar_x = (self.screen_w - bar_w) // 2
        bar_y = self.screen_h // 2
        cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)

        fill_w = int(bar_w * (progress / total)) if total > 0 else 0
        if fill_w > 0:
            cv2.rectangle(img, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), (0, 255, 0), -1)

        cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (255, 255, 255), 2)

        percent = int((progress / total) * 100) if total > 0 else 0
        cv2.putText(img, "{}%".format(percent), (self.screen_w//2 - 40, bar_y + bar_h + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)

        cv2.putText(img, status_text, (self.screen_w//2 - 300, self.screen_h//2 + 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)

        counter = "Kamera: {}/{}".format(progress, total)
        cv2.putText(img, counter, (self.screen_w//2 - 150, self.screen_h//2 + 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (150, 150, 150), 2, cv2.LINE_AA)

        self.show_frame(img)
        cv2.waitKey(1)

    def build_grid(self, frames, cam_count, cell_w, cell_h):
        """Assembles frames into an N x M grid, scaling to fit the monitor resolution."""
        if cam_count == 0:
            return np.zeros((self.screen_h, self.screen_w, 3), dtype=np.uint8)

        cols = math.ceil(math.sqrt(cam_count))
        rows = math.ceil(cam_count / cols)

        black_screen = np.zeros((cell_h, cell_w, 3), dtype=np.uint8)

        # Pad remaining cells with black frames
        padded_frames = list(frames)
        while len(padded_frames) < cols * rows:
            padded_frames.append(black_screen.copy())

        grid_rows = []
        for r in range(rows):
            start = r * cols
            end = start + cols
            row_frames = padded_frames[start:end]
            grid_rows.append(cv2.hconcat(row_frames))

        grid = cv2.vconcat(grid_rows)
        
        # Fit grid onto screen size while preserving aspect ratio
        grid_h, grid_w = grid.shape[:2]
        scale = min(self.screen_w / grid_w, self.screen_h / grid_h)
        if scale > 0:
            new_w = max(1, int(grid_w * scale))
            new_h = max(1, int(grid_h * scale))
            grid = cv2.resize(grid, (new_w, new_h))
            
        return grid

    def build_idle_screen(self, idle_time):
        """Renders the aesthetic screen saver with floating digital clock."""
        display = np.zeros((self.screen_h, self.screen_w, 3), dtype=np.uint8)

        cv2.putText(display, "OKNA KAMER POGASHENY", (self.screen_w//2 - 300, self.screen_h//2 - 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 100, 100), 2, cv2.LINE_AA)

        off_time = int(idle_time)
        mins = off_time // 60
        secs = off_time % 60
        time_str = "Prostoy: {:02d}:{:02d}".format(mins, secs)
        cv2.putText(display, time_str, (self.screen_w//2 - 150, self.screen_h//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (80, 80, 80), 2, cv2.LINE_AA)

        clock = time.strftime("%H:%M:%S")
        cv2.putText(display, clock, (self.screen_w//2 - 100, self.screen_h//2 + 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 2, cv2.LINE_AA)

        cv2.putText(display, "Dvoynoy klik — otkryt kameru | ESC — vyhod", (self.screen_w//2 - 400, self.screen_h - 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2, cv2.LINE_AA)
        cv2.putText(display, "Dvoynoy klik v rezhime kamery — vozvrat v setku", (self.screen_w//2 - 450, self.screen_h - 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2, cv2.LINE_AA)

        return display

    def _mouse_callback(self, event, x, y, flags, param):
        """Internal callback hooked to OpenCV mouse click events."""
        is_double_click = False
        
        if event == cv2.EVENT_LBUTTONDBLCLK:
            is_double_click = True
        elif event == cv2.EVENT_LBUTTONDOWN:
            current_time = time.time()
            dx = x - self.last_click_pos[0]
            dy = y - self.last_click_pos[1]
            dist = math.sqrt(dx*dx + dy*dy)

            if (current_time - self.last_click_time) < self.DOUBLE_CLICK_DELAY and dist < self.DOUBLE_CLICK_RADIUS:
                is_double_click = True

            self.last_click_time = current_time
            self.last_click_pos = (x, y)

        if is_double_click:
            # We callback to main orchestrator
            self.on_double_click_cam_callback(x, y)

    def get_camera_index_from_coordinates(self, x, y, cam_count, cell_w, cell_h):
        """Translates screen pixel coordinates to a corresponding camera index."""
        if cam_count == 0:
            return -1

        cols = math.ceil(math.sqrt(cam_count))
        rows = math.ceil(cam_count / cols)

        grid_w_total = cols * cell_w
        grid_h_total = rows * cell_h

        scale = min(self.screen_w / grid_w_total, self.screen_h / grid_h_total)
        if scale <= 0:
            scale = 1.0

        actual_grid_w = int(grid_w_total * scale)
        actual_grid_h = int(grid_h_total * scale)

        offset_x = (self.screen_w - actual_grid_w) // 2
        offset_y = (self.screen_h - actual_grid_h) // 2

        if x < offset_x or y < offset_y:
            return -1
        if x > offset_x + actual_grid_w or y > offset_y + actual_grid_h:
            return -1

        rel_x = x - offset_x
        rel_y = y - offset_y

        cell_w_scaled = actual_grid_w // cols
        cell_h_scaled = actual_grid_h // rows

        if cell_w_scaled == 0 or cell_h_scaled == 0:
            return -1

        col = rel_x // cell_w_scaled
        row = rel_y // cell_h_scaled

        idx = int(row * cols + col)
        if idx < cam_count:
            return idx
            
        return -1
