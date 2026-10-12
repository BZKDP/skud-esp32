# -*- coding: utf-8 -*-
# Выгружает готовые записи MediaMTX (media/rec/<cam>/*.mp4) в S3 и удаляет локально.
# Хранение в S3 — 7 дней (чистка раз в час). Запуск задачей skud-rec-upload (singleton).
import os, sys, time, glob, datetime, ctypes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3lite

REC_ROOT = r"C:\Users\Administrator\Desktop\skud\media\rec"
CAMS = ["outdoor", "sklad", "pack"]
MIN_AGE_S = 25          # файл готов, если не менялся > 25с (сегмент закрыт, начался следующий)
SCAN_EVERY = 30
RETENTION_DAYS = 7
RETENTION_EVERY = 3600


def log(m):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), m, flush=True)


def upload_ready():
    now = time.time()
    n = 0
    for cam in CAMS:
        d = os.path.join(REC_ROOT, cam)
        if not os.path.isdir(d):
            continue
        for fp in sorted(glob.glob(os.path.join(d, "*.mp4"))):
            try:
                if now - os.path.getmtime(fp) < MIN_AGE_S:
                    continue
                size = os.path.getsize(fp)
                if size == 0:
                    os.remove(fp)
                    continue
                base = os.path.basename(fp)
                key = "rec/%s/%s" % (cam, base)
                st = s3lite.put_object_file(key, fp, "video/mp4")
                if st in (200, 201):
                    os.remove(fp)
                    n += 1
                    log("uploaded %s (%d KB)" % (key, size // 1024))
                else:
                    log("put failed %s status=%s" % (key, st))
            except Exception as e:
                log("err %s: %s" % (fp, e))
    if n:
        log("scan done, uploaded %d" % n)
    return n


def retention():
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=RETENTION_DAYS)
    try:
        objs = s3lite.list_objects("rec/")
    except Exception as e:
        log("retention list err: %s" % e)
        return
    deleted = 0
    for o in objs:
        if o["key"].startswith("rec/_selftest"):
            continue
        lm = o.get("modified", "")
        try:
            t = datetime.datetime.strptime(lm[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        if t < cutoff:
            try:
                s3lite.delete_object(o["key"])
                deleted += 1
            except Exception as e:
                log("del err %s: %s" % (o["key"], e))
    if deleted:
        log("retention: deleted %d objects older than %dd" % (deleted, RETENTION_DAYS))


def main():
    # singleton — не плодить дубли аплоадера. use_last_error=True — иначе ctypes
    # может сбросить LastError между вызовами и дубль проскочит.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW(None, False, "Local\\skud-rec-uploader")  # Local: без привилегии, задачи в одной сессии
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("uploader уже запущен, выход")
        return
    log("s3_uploader старт (cams=%s, retention=%dd)" % (",".join(CAMS), RETENTION_DAYS))
    last_ret = 0.0
    while True:
        try:
            upload_ready()
        except Exception as e:
            log("scan err: %s" % e)
        if time.time() - last_ret > RETENTION_EVERY:
            retention()
            last_ret = time.time()
        time.sleep(SCAN_EVERY)


if __name__ == "__main__":
    main()
