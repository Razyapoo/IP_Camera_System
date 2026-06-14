# -*- coding: utf-8 -*-
import sys
import os
import time
import signal
import logging
import threading
import cv2
import numpy as np

# Import our custom modules
from src.config import ConfigManager
from src.camera import Camera
from src.motion import MotionDetector
from src.ui import AppUI, get_screen_resolution

logger = logging.getLogger("Main")

class CameraSystemApp:
    """Core application orchestrator that manages lifecycles and coordinates config, cameras, motion, and UI."""
    def __init__(self):
        self.config_manager = ConfigManager()
        self.cameras = []
        self.motion_detector = MotionDetector(self.config_manager.motion_threshold)
        
        # State variables
        self.shutdown_requested = False
        self.single_camera_mode = False
        self.single_camera_index = -1
        self.cameras_active = True
        self.last_motion_time = time.time()
        
        # App UI
        self.ui = AppUI(
            self.config_manager,
            on_double_click_cam_callback=self._handle_double_click_on_grid,
            on_double_click_close_callback=self._close_single_camera
        )

        # Thread for periodically checking online status of all cameras
        self.network_monitor_thread = None

    def initialize_cameras(self):
        """Instantiates all camera objects from configuration."""
        cameras_config = self.config_manager.cameras
        for i, cam_cfg in enumerate(cameras_config):
            cam = Camera(i, cam_cfg, self.config_manager)
            self.cameras.append(cam)
        logger.info("Initialized %d cameras.", len(self.cameras))

    def run_startup_sequence(self):
        """Runs the 3-stage initialization process with live visual feedback."""
        self.ui.init_window()
        total_steps = len(self.cameras) * 2 + 1
        current_step = 0

        # === STAGE 1: Network socket check ===
        self.ui.show_startup_screen(current_step, total_steps, "Proverka seti...")
        
        # Quick parallel check at startup using threads to make loading snappy
        threads = []
        for cam in self.cameras:
            t = threading.Thread(target=cam.check_online_status)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        for i, cam in enumerate(self.cameras):
            current_step += 1
            status = "OK" if cam.is_online_cached else "OFFLINE"
            status_text = "Kamera {} ({}): {}".format(i+1, cam.host, status)
            self.ui.show_startup_screen(current_step, total_steps, status_text)
            time.sleep(0.05)

        # === STAGE 2: RTSP Connection & Brand Auto-Discovery ===
        logger.info("Connecting to RTSP streams...")
        for i, cam in enumerate(self.cameras):
            if cam.is_online_cached:
                if cam.brand == "auto":
                    # Brand detection will run in the main thread during startup loading
                    # to keep feedback visible. It updates JSON atomically once resolved.
                    detected = cam.detect_brand_at_startup()
                    if detected:
                        status = "{} {} OK".format(cam.brand.upper(), cam.format.upper())
                    else:
                        status = "BRAND FAIL"
                else:
                    # Test connection
                    url = cam.get_url("grid")
                    fmt = cam.format
                    
                    # Temporarily verify stream
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        cap.release()
                        if ret and frame is not None:
                            cam.rtsp_working = True
                            status = "{} {} OK".format(cam.brand.upper(), cam.format.upper())
                        else:
                            status = "451 ERROR"
                    else:
                        cap.release()
                        status = "451 ERROR"
            else:
                status = "PING FAIL"

            current_step += 1
            status_text = "Kamera {}: {}".format(i+1, status)
            self.ui.show_startup_screen(current_step, total_steps, status_text)
            time.sleep(0.05)

        # === STAGE 3: Final Startup Trigger ===
        current_step += 1
        self.ui.show_startup_screen(current_step, total_steps, "Zapusk...")
        time.sleep(0.5)

        # Start grid streams for all cameras
        for cam in self.cameras:
            cam.start_stream("grid")

        # Spawn background network daemon monitor thread
        self.network_monitor_thread = threading.Thread(target=self._network_monitor_loop, daemon=True)
        self.network_monitor_thread.start()

    def _network_monitor_loop(self):
        """Daemon thread loop that checks network status of all cameras every 5 seconds."""
        while not self.shutdown_requested:
            for cam in self.cameras:
                if self.shutdown_requested:
                    break
                cam.check_online_status()
            time.sleep(5.0)

    def main_loop(self):
        """The core application main loop executing window modes and checking event signals."""
        logger.info("[MAIN] Main loop started.")
        W, H = self.config_manager.grid_resolution
        idle_timeout = self.config_manager.idle_timeout

        while not self.shutdown_requested:
            if self.single_camera_mode:
                # === SINGLE CAMERA MODE ===
                cam = self.cameras[self.single_camera_index]
                win_name = "Camera {} - {}".format(self.single_camera_index + 1, cam.name)

                # Fetch full-screen styled frame
                frame = cam.get_latest_frame(self.ui.screen_w, self.ui.screen_h)

                # Draw status info-bar on top of the fullscreen display
                if cam.rtsp_working and frame is not None:
                    info = "Kamera {}: {} | brand: {} | format: {} | stream: {} | Dvoynoy klik — vozvrat".format(
                        self.single_camera_index + 1, cam.name, cam.brand, cam.format, cam.fullscreen_stream)
                    cv2.putText(frame, info, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

                self.ui.show_frame(frame, title=win_name)

                key = cv2.waitKey(30)
                if key == 27:  # ESC to exit
                    break
                elif key != -1:
                    # Intercept keyboard hits to reset idle timer
                    self._reset_idle_timer()

            else:
                # === GRID MODE ===
                frames = []
                raw_frames_dict = {}

                for i, cam in enumerate(self.cameras):
                    # Fetch grid frame
                    frame = cam.get_latest_frame(W, H)
                    frames.append(frame)

                    # Store raw unscaled frame for robust motion analysis
                    with cam.frame_lock:
                        if cam.last_frame is not None:
                            raw_frames_dict[i] = cam.last_frame.copy()

                # Robust motion analysis on raw frames
                motion_detected = self.motion_detector.detect_motion(raw_frames_dict)
                if motion_detected:
                    self._reset_idle_timer()

                # Manage screensaver/idle state transitions
                idle_time = time.time() - self.last_motion_time
                if idle_time > idle_timeout:
                    self.cameras_active = False

                if self.cameras_active:
                    display = self.ui.build_grid(frames, len(self.cameras), W, H)
                else:
                    display = self.ui.build_idle_screen(idle_time)

                self.ui.show_frame(display)

                key = cv2.waitKey(1)
                if key == 27:  # ESC to exit
                    break
                elif key != -1:
                    self._reset_idle_timer()

        self.cleanup()

    def _reset_idle_timer(self):
        """Resets the inactivity screensaver timer."""
        self.last_motion_time = time.time()
        self.cameras_active = True

    def _handle_double_click_on_grid(self, x, y):
        """Grid double-click handler. Discovers camera under cursor and triggers full-screen mode."""
        if self.single_camera_mode:
            self._close_single_camera()
        else:
            W, H = self.config_manager.grid_resolution
            cam_idx = self.ui.get_camera_index_from_coordinates(x, y, len(self.cameras), W, H)
            if cam_idx >= 0:
                self._open_single_camera(cam_idx)

    def _open_single_camera(self, cam_index):
        """Transitions application into smooth, full-screen, single-camera view."""
        if cam_index < 0 or cam_index >= len(self.cameras):
            return

        logger.info("[SINGLE] Transitioning to single-camera view for camera %d", cam_index + 1)
        
        # Pause background streaming for other cameras to minimize bandwidth
        for i, cam in enumerate(self.cameras):
            if i != cam_index:
                cam.paused = True
            else:
                cam.paused = False

        self.single_camera_mode = True
        self.single_camera_index = cam_index

        # Destroy primary grid window
        self.ui.destroy_window()

        # Re-initialize stream for the active camera with its fullscreen configuration
        target_cam = self.cameras[cam_index]
        target_cam.start_stream("fullscreen")

        # Open dedicated full-screen window for the active camera
        win_name = "Camera {} - {}".format(cam_index + 1, target_cam.name)
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        if self.config_manager.fullscreen:
            cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        cv2.setMouseCallback(win_name, self.ui._mouse_callback)

    def _close_single_camera(self):
        """Restores grid monitoring, stopping full-screen camera reader and restarting other cameras."""
        if not self.single_camera_mode:
            return

        logger.info("[SINGLE] Closing single-camera view, returning to grid.")
        
        # Stop fullscreen stream thread for the target camera
        target_cam = self.cameras[self.single_camera_index]
        target_cam.stop_stream()

        # Destroy single-view window
        win_name = "Camera {} - {}".format(self.single_camera_index + 1, target_cam.name)
        self.ui.destroy_window(win_name)

        # Unpause all cameras and restore their grid stream readers
        for cam in self.cameras:
            cam.paused = False
            cam.start_stream("grid")

        self.single_camera_mode = False
        self.single_camera_index = -1

        # Re-initialize the primary grid window
        self.ui.init_window()
        self._reset_idle_timer()

    def cleanup(self):
        """Ensures systematic stopping of background reader threads, releasing cv2.VideoCaptures and closing windows."""
        logger.info("[CLEANUP] Stopping camera streams...")
        self.shutdown_requested = True
        
        for cam in self.cameras:
            try:
                cam.stop_stream()
            except Exception as e:
                logger.debug("Error stopping stream for camera %d: %s", cam.index + 1, e)

        logger.info("[CLEANUP] Closing UI windows...")
        try:
            cv2.destroyAllWindows()
            cv2.waitKey(100)
        except Exception:
            pass

        logger.info("[CLEANUP] System resources cleanly released.")

def setup_signal_handlers(app):
    """Binds SIGINT/SIGTERM OS signals to clean shutdown procedure."""
    def handle_signal(sig, frame):
        logger.info("[SIGNAL] Capture signal %d, initiating clean shutdown.", sig)
        app.shutdown_requested = True
        app.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

if __name__ == '__main__':
    app = CameraSystemApp()
    setup_signal_handlers(app)
    
    try:
        app.initialize_cameras()
        app.run_startup_sequence()
        app.main_loop()
    except KeyboardInterrupt:
        logger.info("Application interrupted via keyboard.")
    except Exception as e:
        logger.error("Fatal uncaught exception: %s", e, exc_info=True)
    finally:
        app.cleanup()
