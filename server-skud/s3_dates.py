# -*- coding: utf-8 -*-
# Сколько объектов записи СКЛАДА лежит в S3 по каждой дате.
import sys
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import s3lite

for d in ["2026-07-12","2026-07-13","2026-07-14","2026-07-15","2026-07-16",
          "2026-07-17","2026-07-18","2026-07-19","2026-07-20","2026-07-21","2026-07-22"]:
    try:
        n = sum(1 for _ in s3lite.list_objects("rec/sklad/%s" % d))
        print("sklad %s: %d файлов" % (d, n), flush=True)
    except Exception as e:
        print("sklad %s: ERR %s" % (d, e), flush=True)
