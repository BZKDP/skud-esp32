# -*- coding: utf-8 -*-
import sys
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import s3lite
for d in ["2026-07-04","2026-07-05","2026-07-06","2026-07-07","2026-07-08","2026-07-09","2026-07-10","2026-07-11"]:
    print("sklad %s: %d" % (d, sum(1 for _ in s3lite.list_objects("rec/sklad/%s" % d))), flush=True)
print("sklad TOTAL: %d" % sum(1 for _ in s3lite.list_objects("rec/sklad/")), flush=True)
