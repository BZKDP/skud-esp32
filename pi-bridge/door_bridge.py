#!/usr/bin/env python3
# Мост СКУД: Raspberry Pi (надёжный Ethernet) <-> ESP32 (USB serial) <-> сервер.
# Pi опрашивает сервер раз в секунду и при команде open шлёт ESP32 по serial
# "open" (дверь 1) или "open2" (дверь 2). ESP32 щёлкает реле локально.
# Так мы полностью убираем ненадёжный Wi-Fi ESP32 из цепочки.

import time
import sys
import os
import json
import urllib.request
import urllib.error
import glob

import serial  # pip3 install pyserial

# ===== Конфиг =====
POLL_URL = os.getenv("SKUD_SERVER_URL", "http://<SERVER_HOST>:8097") + "/api/poll"
DEVICE_TOKEN = os.getenv("SKUD_DEVICE_TOKEN", "")
POLL_INTERVAL_S = 1.0
HTTP_TIMEOUT_S = 8.0    # терпим кратковременные подвисы сервера (был 4с) — poll не падает по таймауту
BAUD = 115200
# Дверной ESP32 ЗАКРЕПЛЁН по стабильному by-id (CH340) — чтобы не перепутать с камерой
# (она FTDI). Иначе при двух USB-устройствах door-bridge мог схватить порт камеры.
DOOR_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
SERIAL_GLOBS = ["/dev/ttyUSB*", "/dev/ttyACM*"]  # запасной вариант, если by-id исчез


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def find_serial_port():
    if os.path.exists(DOOR_PORT):
        return DOOR_PORT
    # фолбэк: ищем порт, но НЕ камеру (FTDI)
    for pattern in SERIAL_GLOBS:
        for p in sorted(glob.glob(pattern)):
            return p
    return None


def open_serial():
    while True:
        port = find_serial_port()
        if port:
            try:
                ser = serial.Serial(port, BAUD, timeout=1)
                time.sleep(2)  # ESP32 ресетится при открытии порта — ждём загрузку
                log(f"Serial открыт: {port}")
                return ser
            except Exception as e:
                log(f"Не открыть {port}: {e}")
        else:
            log("ESP32 serial-порт не найден (/dev/ttyUSB* | /dev/ttyACM*), жду...")
        time.sleep(2)


def send_cmd(ser, cmd):
    ser.write((cmd + "\n").encode())
    ser.flush()
    log(f"-> ESP32: {cmd}")


def poll_once():
    url = f"{POLL_URL}?token={DEVICE_TOKEN}&src=pi-bridge"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        return json.loads(r.read().decode())


def main():
    log("door_bridge запущен")
    ser = open_serial()
    fails = 0
    while True:
        try:
            data = poll_once()
            fails = 0
            if data.get("cmd") == "open":
                door = data.get("door", 1)
                base = {2: "open2", 3: "open3", 4: "open4"}.get(door, "open")
                dur = data.get("duration_ms")
                cmd = base + ((" " + str(int(dur))) if dur else "")
                log(f"Сервер: open door={door} dur={dur} -> {cmd}")
                try:
                    send_cmd(ser, cmd)
                except Exception as e:
                    log(f"Ошибка записи в serial: {e}, переоткрываю порт")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = open_serial()
                    send_cmd(ser, cmd)
        except urllib.error.URLError as e:
            fails += 1
            if fails == 1 or fails % 10 == 0:
                log(f"poll сеть-ошибка #{fails}: {e}")
        except Exception as e:
            fails += 1
            if fails == 1 or fails % 10 == 0:
                log(f"poll ошибка #{fails}: {e}")
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
