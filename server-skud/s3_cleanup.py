# -*- coding: utf-8 -*-
# Отчёт по объёму S3 по камерам и удаление всех, кроме sklad (склад продуктов).
#   python s3_cleanup.py            -> только отчёт (счёт + размер)
#   python s3_cleanup.py DELETE     -> удалить rec/outdoor/* и rec/pack/*, sklad НЕ трогать
import sys, concurrent.futures
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import s3lite

MODE = sys.argv[1] if len(sys.argv) > 1 else "report"
CAMS = ["outdoor", "sklad", "pack"]

def scan(prefix):
    n = 0; size = 0; keys = []
    for o in s3lite.list_objects(prefix):
        n += 1
        try: size += int(o.get("size", 0) or 0)
        except Exception: pass
        keys.append(o["key"])
    return n, size, keys

allkeys = {}
for cam in CAMS:
    n, size, keys = scan("rec/%s/" % cam)
    allkeys[cam] = keys
    print("rec/%s/: %d объектов, %.1f ГБ" % (cam, n, size/1e9), flush=True)

if MODE == "DELETE":
    todel = allkeys["outdoor"] + allkeys["pack"]
    print("=== УДАЛЯЮ outdoor+pack: %d объектов (sklad НЕ трогаю) ===" % len(todel), flush=True)
    d = 0; e = 0
    def dele(k):
        try:
            s3lite.delete_object(k); return True
        except Exception:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        for i, ok in enumerate(ex.map(dele, todel)):
            if ok: d += 1
            else: e += 1
            if (i + 1) % 3000 == 0:
                print("...%d/%d  удалено=%d ошибок=%d" % (i + 1, len(todel), d, e), flush=True)
    print("=== ГОТОВО: удалено=%d ошибок=%d ===" % (d, e), flush=True)
else:
    print("(режим отчёта. для удаления: python s3_cleanup.py DELETE)", flush=True)
