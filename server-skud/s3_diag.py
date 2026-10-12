# -*- coding: utf-8 -*-
import sys, datetime, urllib.error
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import s3lite

print("server_UTC:", datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
try: print("s3lite_now:", s3lite._now())
except Exception as e: print("_now err:", e)
print("endpoint:", s3lite.S3_ENDPOINT, "bucket:", s3lite.BUCKET, "region:", s3lite.REGION)
acc = s3lite.ACCESS or ""
sec = s3lite.SECRET or ""
print("ACCESS_prefix:", (acc[:6] if acc else "EMPTY"), "ACCESS_len:", len(acc), "SECRET_len:", len(sec))

def probe(label, fn):
    try:
        r = fn()
        print(f"[{label}] OK ->", (len(r) if hasattr(r, '__len__') else r))
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode('utf-8', 'replace')
        except Exception: pass
        print(f"[{label}] HTTP {e.code} {e.reason}")
        print("   body:", body[:900].replace("\n", " "))
    except Exception as e:
        print(f"[{label}] ERR {type(e).__name__}: {e}")

probe("LIST rec/", lambda: s3lite.list_objects(prefix="rec/", max_keys=3))
probe("PUT test", lambda: s3lite.put_object("rec/_diag_test.txt", b"diag", "text/plain"))
