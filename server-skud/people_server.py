#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Счётчик людей НА СЕРВЕРЕ (мощнее Pi). Читает ЛОКАЛЬНЫЙ RTSP MediaMTX
# (rtsp://127.0.0.1:8557/outdoor, полный fps, низкая задержка — поток уже тут),
# детектит YOLOv8s + ByteTrack, шлёт счёт и аннотированный кадр на локальный API.
# Запуск задачей планировщика skud-people (run-people.ps1) в while-цикле с авто-рестартом.

import os, sys, time, json, threading, urllib.request
import cv2
from ultralytics import YOLO

API = os.getenv("SKUD_API", "http://127.0.0.1:8097")
RTSP = os.getenv("SKUD_RTSP", "rtsp://127.0.0.1:8557/outdoor")
PEOPLE_URL = API + "/api/people/count?token=" + os.getenv("SKUD_PEOPLE_TOKEN", "")
VIZ_CAM = os.getenv("SKUD_VIZ_CAM", "counter")          # тест: counter_test, чтобы не трогать боевую
REPORT_ENABLED = os.getenv("SKUD_REPORT", "1") == "1"   # тест: 0 — не писать в статистику сайта
VIZ_URL = API + "/api/cam/push?token=" + os.getenv("SKUD_CAM_TOKEN", "") + "&cam=" + VIZ_CAM

# Модель: если рядом лежит экспортированная OpenVINO-модель (быстрее на CPU в 2-2.5р) — берём её,
# иначе обычный yolov8s.pt. Можно переопределить через SKUD_MODEL.
_HERE = os.path.dirname(os.path.abspath(__file__))
_OV_DIR = os.path.join(_HERE, "yolov8s_openvino_model")
MODEL = os.getenv("SKUD_MODEL") or (_OV_DIR if os.path.isdir(_OV_DIR) else "yolov8s.pt")
CONF = float(os.getenv("SKUD_CONF", "0.30"))
IMGSZ = int(os.getenv("SKUD_IMGSZ", "736"))
CONFIRM_FRAMES = int(os.getenv("SKUD_CONFIRM", "3"))   # ниже, чем на Pi — ловим быстрых (самокаты)
ROTATE180 = os.getenv("SKUD_ROTATE", "1") == "1"       # камера висит перевёрнуто
REPORT_EVERY = 5.0
VIZ_EVERY = float(os.getenv("SKUD_VIZ_EVERY", "0.4"))  # сервер мощнее → картинка обновляется чаще

# RTSP по TCP, маленький буфер — минимальная задержка
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


class Grabber(threading.Thread):
    """Держит только самый свежий кадр (без накопления лага)."""
    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url; self.frame = None; self.lock = threading.Lock(); self.run_flag = True

    def run(self):
        while self.run_flag:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if not cap.isOpened():
                log("RTSP не открылся, повтор 3с"); cap.release(); time.sleep(3); continue
            log("RTSP открыт")
            fails = 0
            while self.run_flag:
                ok, fr = cap.read()
                if not ok:
                    fails += 1
                    if fails > 30:
                        break
                    continue
                fails = 0
                with self.lock:
                    self.frame = fr
            cap.release(); time.sleep(2)

    def latest(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()


def report_people(n):
    body = json.dumps({"n": n}).encode()
    req = urllib.request.Request(PEOPLE_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=4).read()


def push_viz(jpeg):
    req = urllib.request.Request(VIZ_URL, data=jpeg, method="POST",
                                 headers={"Content-Type": "image/jpeg"})
    urllib.request.urlopen(req, timeout=4).read()


def main():
    # БОЕВАЯ СИСТЕМА: камера+двери в приоритете. Счётчик НЕ должен зажать машину.
    # 1) понизить приоритет процесса (Windows) — веб-сервер/двери всегда впереди.
    if sys.platform == "win32":
        try:
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
    # 2) оставить минимум 2 ядра веб-серверу/двери/камере.
    try:
        import torch
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
    except Exception:
        pass
    try:
        cv2.setNumThreads(2)
    except Exception:
        pass

    log("people_server старт, модель " + MODEL)
    model = YOLO(MODEL)
    log("модель загружена")
    grab = Grabber(RTSP); grab.start()

    id_frames = {}      # сколько кадров видели трек
    counted = set()     # уже посчитанные треки
    pending = 0
    today = 0
    last_report = last_viz = 0.0
    fps_t = time.time(); fps_n = 0

    while True:
        frame = grab.latest()
        if frame is None:
            time.sleep(0.05); continue
        if ROTATE180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        H, W = frame.shape[:2]

        res = model.track(frame, persist=True, classes=[0], conf=CONF, imgsz=IMGSZ,
                          tracker="bytetrack.yaml", verbose=False)
        now = time.time()
        fps_n += 1
        if now - fps_t >= 10:
            log(f"fps~{fps_n/(now-fps_t):.1f}, today={today}, tracks={len(counted)}")
            fps_t = now; fps_n = 0

        boxes = res[0].boxes
        ids = boxes.id
        annotated = frame
        if ids is not None:
            xyxy = boxes.xyxy.cpu().numpy()
            idl = ids.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), tid in zip(xyxy, idl):
                tid = int(tid)
                id_frames[tid] = id_frames.get(tid, 0) + 1
                if id_frames[tid] == CONFIRM_FRAMES and tid not in counted:
                    counted.add(tid); pending += 1; today += 1
                done = tid in counted
                col = (45, 212, 167) if done else (0, 200, 255)
                cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
                cv2.putText(annotated, f"#{tid}", (int(x1), int(y1) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

        cv2.putText(annotated, f"people today: {today}", (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)

        if pending > 0 and now - last_report > REPORT_EVERY:
            if REPORT_ENABLED:
                try:
                    report_people(pending); pending = 0; last_report = now
                except Exception as e:
                    log(f"report err: {e}")
            else:
                pending = 0; last_report = now   # тест: считаем внутри, но в статистику не шлём

        if now - last_viz > VIZ_EVERY:
            small = cv2.resize(annotated, (960, int(960 * H / W)))
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                try:
                    push_viz(buf.tobytes()); last_viz = now
                except Exception as e:
                    log(f"viz err: {e}")

        if len(id_frames) > 1500:
            id_frames.clear()
            if len(counted) > 6000:
                counted.clear()


if __name__ == "__main__":
    main()
