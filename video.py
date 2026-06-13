# -*- coding: utf-8 -*-
"""
Backward compatibility launcher for the refactored IP Camera System.
Delegates execution to the new modular, multi-threaded main.py application.
"""
import sys
import logging
from main import CameraSystemApp, setup_signal_handlers

if __name__ == '__main__':
    # Initialize standard logging configuration
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
    )
    
    app = CameraSystemApp()
    setup_signal_handlers(app)
    
    try:
        app.initialize_cameras()
        app.run_startup_sequence()
        app.main_loop()
    except KeyboardInterrupt:
        logging.info("Application interrupted by user.")
    except Exception as e:
        logging.critical("Fatal error occurred in launcher: %s", e, exc_info=True)
    finally:
        app.cleanup()
