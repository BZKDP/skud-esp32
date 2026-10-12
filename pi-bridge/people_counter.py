#!/usr/bin/env python3
# Счётчик людей: тянет уличный поток с сервера (RTSP), детектит людей (YOLOv8n),
# трекает (ByteTrack), считает пересечения виртуальной ЛИНИИ и шлёт цифры на сервер.
# Плюс рисует рамки+ID+линию и шлёт аннотированный кадр как камеру "counter"
# (чтобы видеть глазами и крутить линию). Линия — в долях кадра (line.json).
#
# Запуск из venv: /opt/people-counter/venv/bin/python /opt/people-counter/people_counter.py

import os, re, time, json, threading, urllib.request
import cv2
from ultralytics import YOLO

# Поток берём по HLS с сервера (RTSP/RTMP с этого сервера на Pi не идут; HLS — надёжно).
# Задержка HLS для счётчика не важна — считаем пересечения, не смотрим вживую.
SERVER = os.getenv("SKUD_SERVER_URL", "http://<SERVER_HOST>:8097")
HLS_URL = SERVER + "/cam/outdoor/index.m3u8"
ADMIN_PASS = os.getenv("SKUD_ADMIN_PASSWORD", "")
_CAM_TOKEN = os.getenv("SKUD_CAM_TOKEN", "")
_PEOPLE_TOKEN = os.getenv("SKUD_PEOPLE_TOKEN", "")
PEOPLE_URL = SERVER + "/api/people/count?token=" + _PEOPLE_TOKEN
VIZ_URL = SERVER + "/api/cam/push?token=" + _CAM_TOKEN + "&cam=counter"
# Чистый (без рамок) уличный кадр для сайта — сайт показывает уличную камеру
# ТЕМ ЖЕ поллингом, что и внутреннюю (быстрый старт, без чёрного экрана WebRTC/HLS).
CLEAN_URL = SERVER + "/api/cam/push?token=" + _CAM_TOKEN + "&cam=outdoor_live"
CLEAN_EVERY = 0.2      # ~5 кадров/сек чистого уличного потока
CLEAN_W = 768          # ширина чистого кадра
LINE_FILE = "/opt/people-counter/line.json"


def get_cookie():
    body = json.dumps({"password": ADMIN_PASS}).encode()
    req = urllib.request.Request(SERVER + "/api/admin/login", data=body,
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=8)
    m = re.search(r"skud_adm=([^;]+)", resp.headers.get("Set-Cookie", ""))
    return m.group(1) if m else None

MODEL = "yolov8n.pt"    # закэширована; yolov8s не качается с этого Pi
CONF = 0.25             # порог уверенности
IMGSZ = 896             # баланс: детект + скорость (для стабильного трекинга нужен fps)
CONFIRM_FRAMES = 4      # человек должен продержаться N кадров, чтобы засчитаться (фильтр мельканий)
REPORT_EVERY = 5.0      # сек: как часто шлём накопленных людей
VIZ_EVERY = 1.5         # сек: как часто шлём аннотированный кадр


def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)


def load_line():
    try:
        d = json.load(open(LINE_FILE))
        return float(d["x1"]), float(d["y1"]), float(d["x2"]), float(d["y2"])
    except Exception:
        return 0.10, 0.50, 0.90, 0.50   # по умолчанию горизонтально через середину


class Grabber(threading.Thread):
    """Держит только самый свежий кадр (чтобы не копился лаг)."""
    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url; self.frame = None; self.lock = threading.Lock(); self.run_flag = True
    def run(self):
        while self.run_flag:
            try:
                ck = get_cookie()
            except Exception as e:
                log(f"login err: {e}, повтор 5с"); time.sleep(5); continue
            if not ck:
                log("нет куки, повтор 5с"); time.sleep(5); continue
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "headers;Cookie: skud_adm=" + ck + "\r\n"
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log("HLS не открылся, повтор 5с"); cap.release(); time.sleep(5); continue
            log("HLS открыт")
            fails = 0; t0 = time.time()
            while self.run_flag:
                ok, fr = cap.read()
                if not ok:
                    fails += 1
                    if fails > 30: break
                    continue
                fails = 0
                with self.lock: self.frame = fr
                if time.time() - t0 > 6 * 3600:  # обновить куку до истечения
                    break
            cap.release(); time.sleep(2)
    def latest(self):
        with self.lock: return None if self.frame is None else self.frame.copy()


class CleanPusher(threading.Thread):
    """Шлёт на сервер чистый уличный кадр (без рамок), развёрнутый — для показа на сайте.
    Переиспользует уже декодированный кадр граббера, YOLO не трогает (лёгкий)."""
    def __init__(self, grab):
        super().__init__(daemon=True)
        self.grab = grab
    def run(self):
        while True:
            fr = self.grab.latest()
            if fr is None:
                time.sleep(0.3); continue
            fr = cv2.rotate(fr, cv2.ROTATE_180)   # та же ориентация, что и для счётчика
            H, W = fr.shape[:2]
            small = cv2.resize(fr, (CLEAN_W, int(CLEAN_W * H / W)))
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
            if ok:
                try:
                    req = urllib.request.Request(CLEAN_URL, data=buf.tobytes(), method="POST",
                                                 headers={"Content-Type": "image/jpeg"})
                    urllib.request.urlopen(req, timeout=4).read()
                except Exception:
                    pass
            time.sleep(CLEAN_EVERY)


def side_of_line(px, py, x1, y1, x2, y2):
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


def report_people(n):
    body = json.dumps({"n": n}).encode()
    req = urllib.request.Request(PEOPLE_URL, data=body, method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=4).read()


def push_viz(jpeg):
    req = urllib.request.Request(VIZ_URL, data=jpeg, method="POST", headers={"Content-Type": "image/jpeg"})
    urllib.request.urlopen(req, timeout=4).read()


def main():
    log("people_counter старт, загружаю модель…")
    model = YOLO(MODEL)
    log("модель загружена")
    grab = Grabber(HLS_URL); grab.start()
    CleanPusher(grab).start()   # чистый уличный поток на сайт

    id_frames = {}        # сколько кадров видели каждый трек
    counted = set()       # какие треки уже посчитали (уникальные люди)
    pending = 0
    last_report = last_viz = 0.0
    today_count = 0

    while True:
        frame = grab.latest()
        if frame is None:
            time.sleep(0.2); continue
        frame = cv2.rotate(frame, cv2.ROTATE_180)   # камера висит перевёрнуто — разворачиваем, иначе YOLO не видит людей
        H, W = frame.shape[:2]

        res = model.track(frame, persist=True, classes=[0], conf=CONF, imgsz=IMGSZ,
                          tracker="bytetrack.yaml", verbose=False)
        now = time.time()
        boxes = res[0].boxes
        ids = boxes.id
        annotated = frame
        if ids is not None:
            xyxy = boxes.xyxy.cpu().numpy()
            idl = ids.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), tid in zip(xyxy, idl):
                tid = int(tid)
                id_frames[tid] = id_frames.get(tid, 0) + 1
                # засчитываем НОВОГО человека один раз, когда трек продержался N кадров
                if id_frames[tid] == CONFIRM_FRAMES and tid not in counted:
                    counted.add(tid); pending += 1; today_count += 1
                done = tid in counted
                col = (45, 212, 167) if done else (0, 200, 255)
                cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
                cv2.putText(annotated, f"#{tid}", (int(x1), int(y1) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

        cv2.putText(annotated, f"people today: {today_count}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)

        # отчёт о новых людях
        if pending > 0 and now - last_report > REPORT_EVERY:
            try: report_people(pending); pending = 0; last_report = now
            except Exception as e: log(f"report err: {e}")

        # аннотированный кадр на сервер (для просмотра)
        if now - last_viz > VIZ_EVERY:
            small = cv2.resize(annotated, (960, int(960 * H / W)))
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                try: push_viz(buf.tobytes()); last_viz = now
                except Exception as e: log(f"viz err: {e}")

        # чистим старые треки (чтобы словари не росли)
        if len(id_frames) > 800:
            id_frames.clear()
            if len(counted) > 4000:
                counted.clear()


if __name__ == "__main__":
    main()
