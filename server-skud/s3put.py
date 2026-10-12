# -*- coding: utf-8 -*-
import sys, time, urllib.error
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import s3lite

def test(label, key, data):
    t0 = time.time()
    try:
        r = s3lite.put_object(key, data, "application/octet-stream")
        print(f"[{label}] {len(data)}B PUT OK in {time.time()-t0:.1f}s ->", r)
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code}")
        try: print("   body:", e.read().decode('utf-8','replace')[:500])
        except Exception: pass
    except Exception as e:
        print(f"[{label}] ERR after {time.time()-t0:.1f}s: {type(e).__name__}: {e}")

test("tiny-5B",  "rec/_diag_tiny.txt",  b"hello")
test("small-64K", "rec/_diag_64k.bin",  b"x" * 65536)
test("mid-2M",   "rec/_diag_2m.bin",    b"y" * (2*1024*1024))
