# -*- coding: utf-8 -*-
import s3lite, urllib.request, struct
keys = [o for o in s3lite.list_objects("rec/outdoor/2026-07-04") if o["key"].endswith(".mp4")]
o = keys[-1]  # самый свежий (уже 60с?)
key = o["key"]; print("key:", key, "sizeMB:", round(o["size"]/1e6, 1))
url = s3lite.presign_get(key, 600)
# заголовки range
req = urllib.request.Request(url, headers={"Range": "bytes=0-4095"})
r = urllib.request.urlopen(req, timeout=30)
print("range status:", r.status, "Accept-Ranges:", r.headers.get("Accept-Ranges"), "Content-Range:", r.headers.get("Content-Range"))
data = r.read()

def walk(buf, base=0, depth=0, limit=None):
    off = 0
    while off + 8 <= len(buf):
        size = struct.unpack(">I", buf[off:off+4])[0]
        typ = buf[off+4:off+8].decode("latin1", "replace")
        print("  " * depth + typ + " (" + str(size) + ")")
        if typ in ("moov", "trak", "mdia", "minf", "stbl", "mvex") and 8 < size <= len(buf) - off:
            walk(buf[off+8:off+size], base+off+8, depth+1)
        if size < 8:
            break
        off += size
        if limit and off >= limit:
            break

walk(data)
