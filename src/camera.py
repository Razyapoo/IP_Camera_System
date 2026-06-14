# -*- coding: utf-8 -*-
import os
import cv2
import socket
import logging
import threading
import time
import numpy as np

logger = logging.getLogger("Camera")

BRAND_TEMPLATES = {
    "dahua": {
        "url": "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={stream}",
        "transport": ["tcp", "udp"],
        "description": "Dahua / Amcrest / Uniview"
    },
    "hikvision": {
        "url": "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/{channel}{stream}",
        "transport": ["tcp", "udp"],
        "description": "Hikvision / Safire / EZVIZ"
    },
    "axis": {
        "url": "rtsp://{user}:{password}@{host}:{port}/axis-media/media.amp?videocodec=h264",
        "transport": ["tcp", "udp"],
        "description": "Axis Communications"
    },
    "reolink": {
        "url": "rtsp://{user}:{password}@{host}:{port}/h264Preview_0{channel}_main",
        "transport": ["tcp", "udp"],
        "description": "Reolink"
    },
    "onvif": {
        "url": "rtsp://{user}:{password}@{host}:{port}/onvif{stream}",
        "transport": ["tcp", "udp"],
        "description": "Generic ONVIF"
    },
    "generic": {
        "url": "rtsp://{user}:{password}@{host}:{port}/user={user}&password={password}&channel={channel}&stream={stream}.sdp",
        "transport": ["tcp", "udp"],
        "description": "Generic IP Camera"
    }
}

# Lock to protect modification of the global os.environ for OpenCV Capture Options
env_lock = threading.Lock()

class StreamReader(threading.Thread):
    """Background thread that continuously reads frames from an RTSP stream."""
    def __init__(self, camera, stream_type="grid"):
        super(StreamReader, self).__init__()
        self.camera = camera
        self.stream_type = stream_type
        self.daemon = True
        self.running = False
        self._stop_event = threading.Event()
        self.cap = None

    def run(self):
        self.running = True
        logger.info("StreamReader thread started for camera %d (%s)", self.camera.index + 1, self.camera.name)
        
        consecutive_failures = 0
        
        while not self._stop_event.is_set():
            if self.camera.paused:
                # Release cap if open to free network resource
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                self.camera.rtsp_working = False
                time.sleep(0.5)
                continue

            if not self.camera.is_online_cached:
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                self.camera.rtsp_working = False
                time.sleep(1.0)
                continue

            # Ensure we have a valid brand detected
            if self.camera.brand == "auto":
                # Try to auto-detect brand
                detected = self.camera.detect_brand_at_startup()
                if not detected:
                    self.camera.rtsp_working = False
                    time.sleep(2.0)
                    continue

            # If cap is not initialized or not opened, create it
            if self.cap is None or not self.cap.isOpened():
                url = self.camera.get_url(self.stream_type)
                fmt = self.camera.format
                
                # Protect environmental variable modification
                with env_lock:
                    if fmt == "udp":
                        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'
                    else:
                        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
                    
                    self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not self.cap.isOpened():
                    logger.warning("Failed to open Capture for camera %d (%s)", self.camera.index + 1, self.camera.name)
                    self.camera.rtsp_working = False
                    time.sleep(2.0)
                    continue

            ret, frame = self.cap.read()
            if ret and frame is not None:
                consecutive_failures = 0
                self.camera.rtsp_working = True
                self.camera.update_frame(frame)
            else:
                consecutive_failures += 1
                logger.warning("Empty frame or read error on camera %d (%s), fail count: %d", 
                               self.camera.index + 1, self.camera.name, consecutive_failures)
                
                if consecutive_failures >= 3:
                    if self.cap is not None:
                        self.cap.release()
                        self.cap = None
                    self.camera.rtsp_working = False
                    time.sleep(2.0)
                else:
                    time.sleep(0.1)

        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.running = False
        logger.info("StreamReader thread finished for camera %d (%s)", self.camera.index + 1, self.camera.name)

    def stop(self):
        self._stop_event.set()


class Camera:
    """Represents an IP camera, manages its status, config, and stream reader thread."""
    def __init__(self, index, config_dict, config_manager):
        self.index = index
        self.config_manager = config_manager
        
        # Parse fields from JSON
        self.id = config_dict.get("id", index + 1)
        self.name = config_dict.get("name", "Camera {}".format(index + 1))
        self.brand = config_dict.get("brand", "auto")
        self.host = config_dict.get("host", "")
        self.port = config_dict.get("port", 554)
        self.user = config_dict.get("user", "admin")
        self.password = config_dict.get("password", "")
        self.channel = config_dict.get("channel", 1)
        self.stream = config_dict.get("stream", "1")
        self.fullscreen_stream = config_dict.get("fullscreen_stream", "0")
        self.format = config_dict.get("format", "auto")

        self.is_online_cached = False
        self.rtsp_working = False
        self.paused = False

        self.last_frame = None
        self.frame_lock = threading.Lock()
        self.reader = None

    def check_online_status(self):
        """Checks online status via TCP socket handshake on the RTSP port."""
        if not self.host:
            self.is_online_cached = False
            return False
            
        try:
            # Short timeout to avoid blocking
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.5)
            result = sock.connect_ex((self.host, int(self.port)))
            sock.close()
            self.is_online_cached = (result == 0)
        except Exception as e:
            logger.debug("Exception checking online status for %s: %s", self.host, e)
            self.is_online_cached = False
            
        return self.is_online_cached

    def get_url(self, stream_type="grid"):
        """Builds RTSP URL according to the camera's brand template."""
        brand = self.brand
        if brand not in BRAND_TEMPLATES:
            brand = "generic"

        template = BRAND_TEMPLATES[brand]["url"]
        stream_val = self.fullscreen_stream if stream_type == "fullscreen" else self.stream

        return template.format(
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            channel=self.channel,
            stream=stream_val
        )

    def detect_brand_at_startup(self):
        """
        Attempts to detect the correct camera brand and format by testing all known templates.
        Updates configuration atomically upon success.
        """
        if not self.host or not self.is_online_cached:
            return False

        logger.info("[AUTO-BRAND] Detecting brand for camera %d (%s) on %s...", 
                    self.index + 1, self.name, self.host)
        
        brands_to_try = ["dahua", "hikvision", "axis", "reolink", "onvif", "generic"]

        for brand in brands_to_try:
            logger.info("[AUTO-BRAND] Testing brand: %s (%s)", brand, BRAND_TEMPLATES[brand]["description"])
            
            for transport in ["tcp", "udp"]:
                # Construct test URL
                template = BRAND_TEMPLATES[brand]["url"]
                url = template.format(
                    user=self.user,
                    password=self.password,
                    host=self.host,
                    port=self.port,
                    channel=self.channel,
                    stream=self.stream
                )
                
                with env_lock:
                    if transport == "udp":
                        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'
                    else:
                        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
                        
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

                if cap.isOpened():
                    ret, frame = cap.read()
                    cap.release()
                    
                    if ret and frame is not None:
                        logger.info("[AUTO-BRAND] >>> DETECTED: camera %d is %s (transport: %s)", 
                                    self.index + 1, brand, transport)
                        self.brand = brand
                        self.format = transport
                        # Save back to JSON atomically
                        self.config_manager.update_camera_brand_and_format(self.index, brand, transport)
                        return True
                else:
                    cap.release()

        logger.warning("[AUTO-BRAND] >>> Failed to detect brand for camera %d", self.index + 1)
        return False

    def update_frame(self, frame):
        """Updates the internal frame buffer with thread-safety."""
        with self.frame_lock:
            self.last_frame = frame.copy()

    def get_latest_frame(self, target_w, target_h):
        """
        Retrieves the latest available frame or a localized status image
        if offline, paused, or facing an error.
        """
        with self.frame_lock:
            frame = self.last_frame.copy() if self.last_frame is not None else None

        if self.paused:
            return self._make_status_frame("PAUSED", (128, 128, 0), target_w, target_h)

        if not self.is_online_cached:
            return self._make_status_frame("OFFLINE", (0, 0, 255), target_w, target_h)

        if self.brand == "auto":
            return self._make_status_frame("DETECTING BRAND", (0, 165, 255), target_w, target_h)

        if not self.rtsp_working:
            return self._make_status_frame("NO SIGNAL", (0, 0, 255), target_w, target_h)

        if frame is None:
            return self._make_status_frame("INITIALIZING", (150, 150, 150), target_w, target_h)

        # Scale frame to required target dimensions
        return cv2.resize(frame, (target_w, target_h))

    def _make_status_frame(self, text, color, w, h):
        """Creates a black frame with a text message overlay."""
        img = np.zeros((h, w, 3), dtype=np.uint8)
        full_text = "{} - {}".format(self.name, text)
        # Center text vertically and horizontally
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5
        thickness = 1
        text_size = cv2.getTextSize(full_text, font, scale, thickness)[0]
        text_x = (w - text_size[0]) // 2
        text_y = (h + text_size[1]) // 2
        cv2.putText(img, full_text, (max(5, text_x), text_y), font, scale, color, thickness, cv2.LINE_AA)
        return img

    def start_stream(self, stream_type="grid"):
        """Starts the background stream reader thread."""
        self.stop_stream()
        self.reader = StreamReader(self, stream_type)
        self.reader.start()

    def stop_stream(self):
        """Stops the background stream reader thread."""
        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=1.0)
            self.reader = None
        self.rtsp_working = False
        with self.frame_lock:
            self.last_frame = None
