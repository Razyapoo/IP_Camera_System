# -*- coding: utf-8 -*-
import os
import json
import logging
import tempfile

# Configure standard logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Config")

class ConfigManager:
    """Manages application configurations and provides atomic update capabilities."""
    def __init__(self, config_path=None):
        if config_path is None:
            self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cameras_config.json')
        else:
            self.config_path = config_path
            
        self.config = {}
        self.load_config()

    def load_config(self):
        """Loads configuration from the JSON file."""
        if not os.path.exists(self.config_path):
            logger.error("Configuration file not found: %s", self.config_path)
            raise FileNotFoundError("Configuration file not found at: {}".format(self.config_path))

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            logger.info("Configuration successfully loaded from %s", self.config_path)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON configuration: %s", e)
            raise e
        except Exception as e:
            logger.error("Unexpected error loading configuration: %s", e)
            raise e

    @property
    def cameras(self):
        return self.config.get("cameras", [])

    @property
    def grid_resolution(self):
        return self.config.get("grid_resolution", [640, 360])

    @property
    def idle_timeout(self):
        return self.config.get("idle_timeout", 600)

    @property
    def motion_threshold(self):
        return self.config.get("motion_threshold", 5000)

    @property
    def fullscreen(self):
        return self.config.get("fullscreen", True)

    @property
    def window_title(self):
        return self.config.get("window_title", "Cameras")

    def update_camera_brand_and_format(self, cam_index, brand, fmt):
        """
        Updates the brand and stream transport format for a specific camera
        in the configuration, saving the changes atomically.
        """
        try:
            # First, reload to avoid overwriting changes from other processes/instances
            self.load_config()
            
            cameras = self.config.get("cameras", [])
            if cam_index < len(cameras):
                cameras[cam_index]["brand"] = brand
                cameras[cam_index]["format"] = fmt
                
                # Atomic write: write to temp file then replace
                dir_name = os.path.dirname(os.path.abspath(self.config_path))
                with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8', suffix='.json') as temp_file:
                    json.dump(self.config, temp_file, indent=2, ensure_ascii=False)
                    temp_file_path = temp_file.name
                
                os.replace(temp_file_path, self.config_path)
                logger.info("Configuration updated atomically: camera %d -> brand '%s', format '%s'", 
                            cam_index + 1, brand, fmt)
            else:
                logger.warning("Camera index %d out of bounds for update.", cam_index)
        except Exception as e:
            logger.error("Failed to update camera configuration: %s", e)
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass
            raise e
