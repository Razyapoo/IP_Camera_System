# -*- coding: utf-8 -*-
import cv2
import numpy as np
import os
import subprocess
import platform
import threading
import time
import math
import json
import gc
import signal
import sys

# ============================================================================
# ЗАГРУЗКА КОНФИГУРАЦИИ
# ============================================================================
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cameras_config.json')

if not os.path.exists(CONFIG_PATH):
    print("[ERROR] Ne nayden konfiguratsionnyy fayl: {}".format(CONFIG_PATH))
    sys.exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = json.load(f)

# Камеры из конфига
cameras_config = config.get("cameras", [])
cam_count = len(cameras_config)

# Извлекаем параметры камер (ИСПРАВЛЕНО: enumerate убран, итерируем напрямую)
cam_names = [cam.get("name", "Camera {}".format(i+1)) for i, cam in enumerate(cameras_config)]
cam_brands = [cam.get("brand", "auto") for cam in cameras_config]
cam_hosts = [cam.get("host", "") for cam in cameras_config]
cam_ports = [cam.get("port", 554) for cam in cameras_config]
cam_users = [cam.get("user", "admin") for cam in cameras_config]
cam_passwords = [cam.get("password", "") for cam in cameras_config]
cam_channels = [cam.get("channel", 1) for cam in cameras_config]
cam_streams = [cam.get("stream", "1") for cam in cameras_config]
cam_fullscreen_streams = [cam.get("fullscreen_stream", "0") for cam in cameras_config]
cam_formats = [cam.get("format", "auto") for cam in cameras_config]

# Общие настройки
W, H = config.get("grid_resolution", [640, 360])
IDLE_TIMEOUT = config.get("idle_timeout", 600)
MOTION_THRESHOLD = config.get("motion_threshold", 5000)
FULLSCREEN = config.get("fullscreen", True)
WINDOW_TITLE = config.get("window_title", "Cameras")

# Принудительно TCP
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

black_screen = np.zeros((H, W, 3), dtype=np.uint8)

# ============================================================================
# ШАБЛОНЫ URL ДЛЯ РАЗНЫХ БРЕНДОВ
# ============================================================================
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

# ============================================================================
# СБОРКА RTSP URL ПО БРЕНДУ
# ============================================================================
def build_rtsp_url(cam_index, brand, stream_override=None):
    """Собирает RTSP URL по шаблону бренда камеры."""
    host = cam_hosts[cam_index]
    port = cam_ports[cam_index]
    user = cam_users[cam_index]
    password = cam_passwords[cam_index]
    channel = cam_channels[cam_index]

    if stream_override is not None:
        stream = stream_override
    else:
        stream = cam_streams[cam_index]

    if brand not in BRAND_TEMPLATES:
        brand = "generic"

    template = BRAND_TEMPLATES[brand]["url"]

    # Форматируем URL
    url = template.format(
        user=user,
        password=password,
        host=host,
        port=port,
        channel=channel,
        stream=stream
    )
    return url

def get_grid_url(cam_index, brand=None):
    """URL для сеточного режима."""
    if brand is None:
        brand = cam_brands[cam_index]
    return build_rtsp_url(cam_index, brand, cam_streams[cam_index])

def get_fullscreen_url(cam_index, brand=None):
    """URL для полноэкранного режима."""
    if brand is None:
        brand = cam_brands[cam_index]
    return build_rtsp_url(cam_index, brand, cam_fullscreen_streams[cam_index])

# ============================================================================
# АВТО-ОПРЕДЕЛЕНИЕ БРЕНДА КАМЕРЫ
# ============================================================================
def detect_camera_brand(cam_index):
    """
    Определяет бренд камеры перебором всех известных шаблонов URL.
    Проверяет каждый бренд с tcp и udp транспортом.
    Возвращает название бренда или None.
    Обновляет JSON конфиг при успешном определении.
    """
    host = cam_hosts[cam_index]
    print("[AUTO-BRAND] Opredelenie brenda kamery {} ({}) na {}...".format(
        cam_index + 1, cam_names[cam_index], host))

    # Порядок проверки брендов
    brands_to_try = ["dahua", "hikvision", "axis", "reolink", "onvif", "generic"]

    for brand in brands_to_try:
        print("[AUTO-BRAND] Probuem {} ({})...".format(brand, BRAND_TEMPLATES[brand]["description"]))

        # Пробуем оба транспорта
        for transport in ["tcp", "udp"]:
            url = get_grid_url(cam_index, brand)
            print("[AUTO-BRAND]   URL: {} | transport: {}".format(url, transport))

            cap = create_cap(url, use_tcp=(transport == "tcp"))
            if cap.isOpened():
                # Пробуем прочитать кадр
                ret, frame = cap.read()
                cap.release()

                if ret and frame is not None:
                    print("[AUTO-BRAND] >>> BREND OPREDELEN: {} (transport: {})".format(brand, transport))

                    # Обновляем JSON
                    update_config_brand_and_format(cam_index, brand, transport)
                    return brand, transport
            else:
                cap.release()

    print("[AUTO-BRAND] >>> Ne udalos opredelit brend kamery {}".format(cam_index + 1))
    return None, None

def update_config_brand_and_format(cam_index, brand, fmt):
    """Обновляет бренд и формат в JSON файле конфигурации."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)

        if cam_index < len(cfg.get("cameras", [])):
            cfg["cameras"][cam_index]["brand"] = brand
            cfg["cameras"][cam_index]["format"] = fmt

            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)

            print("[AUTO-BRAND] JSON obnovlen: kamera {} -> brand '{}', format '{}'".format(
                cam_index + 1, brand, fmt))
    except Exception as e:
        print("[AUTO-BRAND] Oshibka obnovleniya JSON: {}".format(e))

# ============================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================================================
host_alive_cache = {}
rtsp_working_cache = {}
cache_lock = threading.Lock()

last_motion_time = time.time()
cameras_active = True

# Режим одиночной камеры
single_camera_mode = False
single_camera_index = -1
single_camera_cap = None
single_camera_paused = [False] * cam_count

# Флаги завершения
shutdown_requested = False

# Двойной клик
last_click_time = 0
last_click_pos = (0, 0)
DOUBLE_CLICK_DELAY = 0.5
DOUBLE_CLICK_RADIUS = 20

# ============================================================================
# ОЧИСТКА ПРИ ЗАВЕРШЕНИИ
# ============================================================================
def cleanup_all():
    global single_camera_cap, caps

    print("[CLEANUP] Nachinaem ochistku...")

    for i in range(cam_count):
        if caps[i] is not None:
            try:
                caps[i].release()
                caps[i] = None
            except:
                pass

    if single_camera_cap is not None:
        try:
            single_camera_cap.release()
            single_camera_cap = None
        except:
            pass

    try:
        cv2.destroyAllWindows()
        cv2.waitKey(100)
    except:
        pass

    gc.collect()

    global prev_frames, frames
    prev_frames = None
    frames = None

    print("[CLEANUP] Ochistka zavershena. RAM i CPU osvobozhdeny.")

def signal_handler(sig, frame):
    global shutdown_requested
    print("\n[SIGNAL] Poluchen signal zaversheniya ({}).".format(sig))
    shutdown_requested = True
    cleanup_all()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================
def get_screen_resolution():
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
        except:
            pass
    elif system == 'Windows':
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except:
            pass
    elif system == 'Darwin':
        try:
            output = subprocess.check_output(['system_profiler', 'SPDisplaysDataType']).decode()
            for line in output.split('\n'):
                if 'Resolution:' in line:
                    parts = line.split(':')[1].strip().split(' x ')
                    return int(parts[0]), int(parts[1])
        except:
            pass
    try:
        import tkinter as tk
        root = tk.Tk()
        width = root.winfo_screenwidth()
        height = root.winfo_screenheight()
        root.destroy()
        return width, height
    except:
        pass
    return 1920, 1080

screen_w, screen_h = get_screen_resolution()
print("Razreshenie ekrana: {}x{}".format(screen_w, screen_h))

def is_host_alive(ip):
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    timeout_param = '-w' if platform.system().lower() == 'windows' else '-W'
    try:
        result = subprocess.run(
            ['ping', param, '1', timeout_param, '1', ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2
        )
        return result.returncode == 0
    except:
        return False

def update_host_cache(ips):
    while not shutdown_requested:
        for ip in ips:
            alive = is_host_alive(ip)
            with cache_lock:
                host_alive_cache[ip] = alive
        time.sleep(5)

def make_black_with_text(text, color=(255,255,255), w=W, h=H):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.putText(img, text, (20, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return img

def create_cap(url, use_tcp=True):
    if use_tcp:
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
    else:
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    return cap

def create_cap_by_format(url, fmt_name):
    """Создает VideoCapture по указанному формату."""
    if fmt_name == "udp":
        return create_cap(url, use_tcp=False)
    else:
        return create_cap(url, use_tcp=True)

# ============================================================================
# ПОЛУЧЕНИЕ КАДРА
# ============================================================================
def get_frame(cap, url, ip, cam_index):
    global single_camera_paused

    with cache_lock:
        alive = host_alive_cache.get(ip, False)
        rtsp_working = rtsp_working_cache.get(cam_index, False)

    if single_camera_paused[cam_index]:
        return make_black_with_text(cam_names[cam_index] + "  PAUSED", (128,128,0)), cap

    if not alive:
        return make_black_with_text(ip + "  OFFLINE", (0,0,255)), cap

    brand = cam_brands[cam_index]
    fmt = cam_formats[cam_index]

    # Если бренд auto — определяем
    if brand == "auto":
        detected_brand, detected_fmt = detect_camera_brand(cam_index)
        if detected_brand:
            cam_brands[cam_index] = detected_brand
            cam_formats[cam_index] = detected_fmt
            brand = detected_brand
            fmt = detected_fmt
            # Пересобираем URL с новым брендом
            url = get_grid_url(cam_index)
        else:
            return make_black_with_text(ip + "  BRAND FAIL", (0,0,255)), cap

    if not rtsp_working:
        if cap is not None:
            cap.release()
        cap = create_cap_by_format(url, fmt)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                with cache_lock:
                    rtsp_working_cache[cam_index] = True
            else:
                cap.release()
                return make_black_with_text(ip + "  NO SIGNAL", (0,0,255)), cap
        else:
            cap.release()
            return make_black_with_text(ip + "  451 ERROR", (0,0,255)), cap

    if cap is None or not cap.isOpened():
        cap = create_cap_by_format(url, fmt)
        if not cap.isOpened():
            with cache_lock:
                rtsp_working_cache[cam_index] = False
            return make_black_with_text(ip + "  451 ERROR", (0,0,255)), cap

    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        cap = create_cap_by_format(url, fmt)
        if not cap.isOpened():
            with cache_lock:
                rtsp_working_cache[cam_index] = False
            return make_black_with_text(ip + "  451 ERROR", (0,0,255)), cap

        ret, frame = cap.read()
        if not ret or frame is None:
            with cache_lock:
                rtsp_working_cache[cam_index] = False
            return make_black_with_text(ip + "  NO VIDEO", (0,0,255)), cap

    frame = cv2.resize(frame, (W, H))
    with cache_lock:
        rtsp_working_cache[cam_index] = True
    return frame, cap

def build_grid(frames, cam_count):
    if cam_count == 0:
        return black_screen.copy()

    cols = math.ceil(math.sqrt(cam_count))
    rows = math.ceil(cam_count / cols)

    while len(frames) < cols * rows:
        frames.append(black_screen.copy())

    grid_rows = []
    for r in range(rows):
        start = r * cols
        end = start + cols
        row_frames = frames[start:end]
        while len(row_frames) < cols:
            row_frames.append(black_screen.copy())
        grid_rows.append(cv2.hconcat(row_frames))

    return cv2.vconcat(grid_rows)

def detect_motion(prev_frames, curr_frames, threshold=MOTION_THRESHOLD):
    if prev_frames is None or len(prev_frames) != len(curr_frames):
        return True

    total_diff = 0
    for prev, curr in zip(prev_frames, curr_frames):
        if prev is None or curr is None:
            continue
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(prev_gray, curr_gray)
        total_diff += np.sum(diff)

    return total_diff > threshold

# ============================================================================
# ИНДИКАТОР ЗАГРУЗКИ
# ============================================================================
def show_startup_screen(progress, total, status_text, screen_w, screen_h):
    img = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)

    title = "ZAGRUZKA SISTEMY..."
    cv2.putText(img, title, (screen_w//2 - 250, screen_h//2 - 100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)

    bar_w = 600
    bar_h = 40
    bar_x = (screen_w - bar_w) // 2
    bar_y = screen_h // 2
    cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)

    fill_w = int(bar_w * (progress / total))
    if fill_w > 0:
        cv2.rectangle(img, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), (0, 255, 0), -1)

    cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (255, 255, 255), 2)

    percent = int((progress / total) * 100)
    cv2.putText(img, "{}%".format(percent), (screen_w//2 - 40, bar_y + bar_h + 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)

    cv2.putText(img, status_text, (screen_w//2 - 300, screen_h//2 + 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)

    counter = "Kamera: {}/{}".format(progress, total)
    cv2.putText(img, counter, (screen_w//2 - 150, screen_h//2 + 180),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (150, 150, 150), 2)

    return img

# ============================================================================
# ОБРАБОТКА ДВОЙНОГО КЛИКА МЫШИ
# ============================================================================
def handle_mouse_click(event, x, y, flags, param):
    global last_click_time, last_click_pos, single_camera_mode, single_camera_index

    if event == cv2.EVENT_LBUTTONDBLCLK:
        if single_camera_mode:
            close_single_camera()
        else:
            cam_idx = get_camera_from_click(x, y)
            if cam_idx >= 0:
                open_single_camera(cam_idx)
    elif event == cv2.EVENT_LBUTTONDOWN:
        current_time = time.time()
        dx = x - last_click_pos[0]
        dy = y - last_click_pos[1]
        dist = math.sqrt(dx*dx + dy*dy)

        if (current_time - last_click_time) < DOUBLE_CLICK_DELAY and dist < DOUBLE_CLICK_RADIUS:
            if single_camera_mode:
                close_single_camera()
            else:
                cam_idx = get_camera_from_click(x, y)
                if cam_idx >= 0:
                    open_single_camera(cam_idx)

        last_click_time = current_time
        last_click_pos = (x, y)

def get_camera_from_click(x, y):
    """Определяет индекс камеры по координатам клика на сетке."""
    cols = math.ceil(math.sqrt(cam_count))
    rows = math.ceil(cam_count / cols)

    grid_w_total = cols * W
    grid_h_total = rows * H

    scale = min(screen_w / grid_w_total, screen_h / grid_h)
    if scale <= 0:
        scale = 1.0

    actual_grid_w = int(grid_w_total * scale)
    actual_grid_h = int(grid_h_total * scale)

    offset_x = (screen_w - actual_grid_w) // 2
    offset_y = (screen_h - actual_grid_h) // 2

    if x < offset_x or y < offset_y:
        return -1
    if x > offset_x + actual_grid_w or y > offset_y + actual_grid_h:
        return -1

    rel_x = x - offset_x
    rel_y = y - offset_y

    cell_w = actual_grid_w // cols
    cell_h = actual_grid_h // rows

    if cell_w == 0 or cell_h == 0:
        return -1

    col = rel_x // cell_w
    row = rel_y // cell_h

    idx = row * cols + col
    if idx < cam_count:
        return idx
    return -1

# ============================================================================
# РЕЖИМ ОДИНОЧНОЙ КАМЕРЫ
# ============================================================================
def open_single_camera(cam_index):
    global single_camera_mode, single_camera_index, single_camera_cap, single_camera_paused

    if cam_index < 0 or cam_index >= cam_count:
        return False

    single_camera_paused = [True] * cam_count

    single_camera_mode = True
    single_camera_index = cam_index

    if single_camera_cap is not None:
        single_camera_cap.release()
        single_camera_cap = None

    brand = cam_brands[cam_index]
    fmt = cam_formats[cam_index]

    # Если бренд auto — определяем
    if brand == "auto":
        detected_brand, detected_fmt = detect_camera_brand(cam_index)
        if detected_brand:
            brand = detected_brand
            fmt = detected_fmt
        else:
            print("[SINGLE] Ne udalos opredelit brend dlya kamery {}".format(cam_index + 1))
            return False

    url = get_fullscreen_url(cam_index, brand)

    print("[SINGLE] Otkryvaem kameru {} ({}) | brand: {} | format: {} | URL: {}".format(
        cam_index + 1, cam_names[cam_index], brand, fmt, url))

    single_camera_cap = create_cap_by_format(url, fmt)

    if not single_camera_cap.isOpened():
        print("[SINGLE] Oshibka podklyucheniya k kamere {}".format(cam_index + 1))
        single_camera_cap.release()
        single_camera_cap = None
        return False

    win_name = "Camera {} - {}".format(cam_index + 1, cam_names[cam_index])
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    if FULLSCREEN:
        cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    cv2.setMouseCallback(win_name, handle_mouse_click)

    return True

def close_single_camera():
    global single_camera_mode, single_camera_index, single_camera_cap, single_camera_paused

    if single_camera_cap is not None:
        single_camera_cap.release()
        single_camera_cap = None

    if single_camera_index >= 0:
        win_name = "Camera {} - {}".format(single_camera_index + 1, cam_names[single_camera_index])
        try:
            cv2.destroyWindow(win_name)
        except:
            pass

    single_camera_paused = [False] * cam_count

    single_camera_mode = False
    single_camera_index = -1

    cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
    if FULLSCREEN:
        cv2.setWindowProperty(WINDOW_TITLE, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    cv2.setMouseCallback(WINDOW_TITLE, handle_mouse_click)

    print("[SINGLE] Vozvrat v setochnyy rezhim")

def get_single_camera_frame():
    global single_camera_cap

    if single_camera_cap is None or not single_camera_cap.isOpened():
        return None

    ret, frame = single_camera_cap.read()
    if not ret or frame is None:
        brand = cam_brands[single_camera_index]
        fmt = cam_formats[single_camera_index]
        url = get_fullscreen_url(single_camera_index, brand)
        single_camera_cap.release()
        single_camera_cap = create_cap_by_format(url, fmt)
        ret, frame = single_camera_cap.read()
        if not ret or frame is None:
            return None

    # Масштабируем под экран, сохраняя пропорции
    fh, fw = frame.shape[:2]
    scale = min(screen_w / fw, screen_h / fh)
    if scale > 0 and scale < 1.0:
        new_w = max(1, int(fw * scale))
        new_h = max(1, int(fh * scale))
        frame = cv2.resize(frame, (new_w, new_h))

    return frame

# ============================================================================
# ГЛАВНЫЙ ЭКРАН ЭНЕРГОСБЕРЕЖЕНИЯ
# ============================================================================
def build_idle_screen(idle_time):
    display = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)

    cv2.putText(display, "OKNA KAMER POGASHENY", (screen_w//2 - 300, screen_h//2 - 100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 100, 100), 2)

    off_time = int(idle_time)
    mins = off_time // 60
    secs = off_time % 60
    time_str = "Prostoy: {:02d}:{:02d}".format(mins, secs)
    cv2.putText(display, time_str, (screen_w//2 - 150, screen_h//2),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (80, 80, 80), 2)

    clock = time.strftime("%H:%M:%S")
    cv2.putText(display, clock, (screen_w//2 - 100, screen_h//2 + 80),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 2)

    cv2.putText(display, "Dvoynoy klik — otkryt kameru | ESC — vyhod", (screen_w//2 - 400, screen_h - 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2)
    cv2.putText(display, "Dvoynoy klik v rezhime kamery — vozvrat v setku", (screen_w//2 - 450, screen_h - 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2)

    return display

# ============================================================================
# ЗАГРУЗКА И ЗАПУСК
# ============================================================================
ips = cam_hosts

# Создаем окно
cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
if FULLSCREEN:
    cv2.setWindowProperty(WINDOW_TITLE, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

cv2.setMouseCallback(WINDOW_TITLE, handle_mouse_click)

# === ЭТАП 1: Проверка сети ===
total_steps = cam_count * 2 + 1
current_step = 0

startup_img = show_startup_screen(current_step, total_steps, "Proverka seti...", screen_w, screen_h)
cv2.imshow(WINDOW_TITLE, startup_img)
cv2.waitKey(100)

for i, ip in enumerate(ips):
    alive = is_host_alive(ip)
    host_alive_cache[ip] = alive
    rtsp_working_cache[i] = False
    current_step += 1

    status = "OK" if alive else "OFFLINE"
    status_text = "Kamera {} ({}): {}".format(i+1, ip, status)
    startup_img = show_startup_screen(current_step, total_steps, status_text, screen_w, screen_h)
    cv2.imshow(WINDOW_TITLE, startup_img)
    cv2.waitKey(50)

# === ЭТАП 2: Подключение RTSP ===
print("Podklyuchenie k kameram...")
caps = [None] * cam_count

for i in range(cam_count):
    if host_alive_cache.get(ips[i], False):
        brand = cam_brands[i]
        fmt = cam_formats[i]

        # Если бренд auto — определяем
        if brand == "auto":
            detected_brand, detected_fmt = detect_camera_brand(i)
            if detected_brand:
                brand = detected_brand
                fmt = detected_fmt
            else:
                status = "BRAND FAIL"
                current_step += 1
                status_text = "Kamera {}: {}".format(i+1, status)
                startup_img = show_startup_screen(current_step, total_steps, status_text, screen_w, screen_h)
                cv2.imshow(WINDOW_TITLE, startup_img)
                cv2.waitKey(100)
                continue

        url = get_grid_url(i, brand)
        cap = create_cap_by_format(url, fmt)
        if cap.isOpened():
            caps[i] = cap
            rtsp_working_cache[i] = True
            status = "{} {} OK".format(brand.upper(), fmt.upper())
        else:
            cap.release()
            status = "451 ERROR"
    else:
        status = "PING FAIL"

    current_step += 1
    status_text = "Kamera {}: {}".format(i+1, status)
    startup_img = show_startup_screen(current_step, total_steps, status_text, screen_w, screen_h)
    cv2.imshow(WINDOW_TITLE, startup_img)
    cv2.waitKey(100)

# === ЭТАП 3: Готово ===
current_step += 1
startup_img = show_startup_screen(current_step, total_steps, "Zapusk...", screen_w, screen_h)
cv2.imshow(WINDOW_TITLE, startup_img)
cv2.waitKey(500)

# Запускаем фоновый поток
cache_thread = threading.Thread(target=update_host_cache, args=(ips,), daemon=True)
cache_thread.start()

# Переменные для отслеживания движения
prev_frames = None
frames = []

# ============================================================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================================================
print("[MAIN] Glavnyy tsikl zapuschen.")
print("[CONTROLS] Dvoynoy klik myshi — otkryt kameru | ESC — vyhod")

try:
    while not shutdown_requested:
        # === РЕЖИМ ОДИНОЧНОЙ КАМЕРЫ ===
        if single_camera_mode:
            frame = get_single_camera_frame()
            win_name = "Camera {} - {}".format(single_camera_index + 1, cam_names[single_camera_index])

            if frame is not None:
                fh, fw = frame.shape[:2]
                display = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)
                y = (screen_h - fh) // 2
                x = (screen_w - fw) // 2
                if y >= 0 and x >= 0:
                    display[y:y+fh, x:x+fw] = frame
                else:
                    display = frame

                # Инфо-панель
                info = "Kamera {}: {} | brand: {} | format: {} | stream: {} | Dvoynoy klik — vozvrat".format(
                    single_camera_index + 1, cam_names[single_camera_index],
                    cam_brands[single_camera_index],
                    cam_formats[single_camera_index],
                    cam_fullscreen_streams[single_camera_index])
                cv2.putText(display, info, (20, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            else:
                display = make_black_with_text("NO SIGNAL", (0,0,255), screen_w, screen_h)

            cv2.imshow(win_name, display)

            key = cv2.waitKey(30)
            if key == 27:
                break

            continue

        # === СЕТОЧНЫЙ РЕЖИМ ===
        frames = []
        for i in range(cam_count):
            brand = cam_brands[i]
            url = get_grid_url(i, brand)
            frame, caps[i] = get_frame(caps[i], url, ips[i], i)
            frames.append(frame)

        # Проверяем движение
        motion_detected = detect_motion(prev_frames, frames)
        prev_frames = [f.copy() if f is not None else None for f in frames]

        if motion_detected:
            last_motion_time = time.time()
            cameras_active = True

        # Проверяем таймаут энергосбережения
        idle_time = time.time() - last_motion_time
        if idle_time > IDLE_TIMEOUT:
            cameras_active = False

        if cameras_active:
            grid = build_grid(frames, cam_count)
            grid_h, grid_w = grid.shape[:2]
            if grid_w > 0 and grid_h > 0 and screen_w > 0 and screen_h > 0:
                scale = min(screen_w / grid_w, screen_h / grid_h)
                if scale > 0:
                    new_w = max(1, int(grid_w * scale))
                    new_h = max(1, int(grid_h * scale))
                    grid = cv2.resize(grid, (new_w, new_h))
            display = grid
        else:
            display = build_idle_screen(idle_time)

        cv2.imshow(WINDOW_TITLE, display)

        key = cv2.waitKey(1)
        if key == 27:
            break
        elif key != -1:
            last_motion_time = time.time()
            cameras_active = True

except KeyboardInterrupt:
    print("\n[MAIN] Prervano polzovatelem.")
except Exception as e:
    print("\n[MAIN] Oshibka: {}".format(e))
finally:
    cleanup_all()

print("[MAIN] Skript zavershen.")
