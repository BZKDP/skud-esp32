#!/usr/bin/env python3
# Мост ESP32-CAM (CameraSerial, JPEG по UART) -> сервер. БЕЗ ЛАГА.
# Камера висит на Pi по USB, шлёт кадры AA55AA55|LEN(uint32 LE)|JPEG @921600.
#
# Антилаг: читатель в отдельном потоке непрерывно парсит кадры и держит ТОЛЬКО
# самый свежий (старые непосланные выбрасываются). Отправитель шлёт свежий кадр
# на сервер. Так в очереди не копятся устаревшие кадры -> задержка минимальна.
# Порт камеры закреплён by-id (FTDI) — чтобы не перепутать с дверью (CH340).

import time
import sys
import os
import struct
import glob
import threading
import urllib.request

import serial  # pyserial

CAM_PORT = os.getenv("SKUD_CAM_PORT", "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART-if00-port0")
BAUD = 921600
_SERVER = os.getenv("SKUD_SERVER_URL", "http://<SERVER_HOST>:8097")
PUSH_URL = _SERVER + "/api/cam/push?token=" + os.getenv("SKUD_CAM_TOKEN", "") + "&cam=indoor"
MAGIC = b"\xAA\x55\xAA\x55"
MAX_JPEG = 600000
POST_INTERVAL = 0.07   # отправляем не чаще ~14 кадров/сек

_latest = None
_lock = threading.Lock()


def log(m):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), m, flush=True)


def find_port():
    if os.path.exists(CAM_PORT):
        return CAM_PORT
    # НЕ хватать первый попавшийся /dev/ttyUSB* — там может оказаться CH340 ДВЕРИ
    # (при переподключении USB камера на миг пропадает, и дверной порт остаётся один).
    # Берём ТОЛЬКО FTDI by-id (камера); дверь (CH340) игнорируем полностью.
    for p in sorted(glob.glob("/dev/serial/by-id/*FTDI*")):
        return p
    return None


def open_serial():
    while True:
        port = find_port()
        if port:
            try:
                s = serial.Serial(port, BAUD, timeout=1)
                time.sleep(2)
                s.reset_input_buffer()
                log(f"camera serial открыт: {port}")
                return s
            except Exception as e:
                log(f"не открыть {port}: {e}")
        else:
            log("порт камеры не найден, жду")
        time.sleep(3)


def read_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def reader_loop():
    """Непрерывно читает кадры и держит только самый свежий в _latest."""
    global _latest
    s = open_serial()
    sync = b""
    while True:
        try:
            b = s.read(1)
            if not b:
                continue
            sync = (sync + b)[-4:]
            if sync != MAGIC:
                continue
            sync = b""
            lenb = read_exact(s, 4)
            if not lenb:
                continue
            length = struct.unpack("<I", lenb)[0]
            if length == 0 or length > MAX_JPEG:
                continue
            jpeg = read_exact(s, length)
            if not jpeg:
                continue
            with _lock:
                _latest = jpeg   # перезапись -> старый непосланный кадр выбрасывается
        except (serial.SerialException, OSError) as e:
            log(f"serial ошибка: {e}, переоткрываю")
            try:
                s.close()
            except Exception:
                pass
            s = open_serial()
            sync = b""


def post_frame(jpeg):
    req = urllib.request.Request(
        PUSH_URL, data=jpeg, method="POST",
        headers={"Content-Type": "image/jpeg"},
    )
    urllib.request.urlopen(req, timeout=4).read()


def main():
    log("cam_serial_bridge запущен (антилаг, 2 потока)")
    threading.Thread(target=reader_loop, daemon=True).start()
    last = None
    fails = 0
    while True:
        with _lock:
            jpeg = _latest
        if jpeg is not None and jpeg is not last:
            last = jpeg
            try:
                post_frame(jpeg)
                fails = 0
            except Exception as e:
                fails += 1
                if fails == 1 or fails % 20 == 0:
                    log(f"POST ошибка #{fails}: {e}")
        time.sleep(POST_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
