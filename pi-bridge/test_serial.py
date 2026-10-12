#!/usr/bin/env python3
# Тест связи Pi <-> ESP32 по serial. Сбрасывает плату, читает баннер,
# и (если передан аргумент) шлёт команду и читает ответ.
# Запуск:  python3 test_serial.py            -> только баннер
#          python3 test_serial.py open2      -> + открыть дверь 2
import sys, time, serial

PORT = "/dev/ttyUSB0"
s = serial.Serial(PORT, 115200, timeout=1)

# Аппаратный сброс ESP32 (RTS=EN, DTR=GPIO0): нормальная загрузка
s.setDTR(False)
s.setRTS(True)
time.sleep(0.15)
s.setRTS(False)
time.sleep(2.0)

def drain(seconds, tag):
    end = time.time() + seconds
    while time.time() < end:
        line = s.readline().decode("utf-8", "replace").rstrip()
        if line:
            print(tag, line, flush=True)

print("--- БАННЕР после сброса ---")
drain(3, "[boot]")

if len(sys.argv) > 1:
    cmd = sys.argv[1]
    print(f"--- шлю команду: {cmd} ---")
    s.write((cmd + "\n").encode())
    s.flush()
    drain(7, "[resp]")

s.close()
