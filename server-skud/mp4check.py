# -*- coding: utf-8 -*-
import s3lite, urllib.request, struct
key = None
for o in s3lite.list_objects("rec/outdoor/2026-07-04"):
    if o["key"].endswith(".mp4"):
        key = o["key"]
        break
print("key:", key)
url = s3lite.presign_get(key, 600)
req = urllib.request.Request(url, headers={"Range": "bytes=0-32767"})
r = urllib.request.urlopen(req, timeout=30)
print("HTTP", r.status, "content-type", r.headers.get("Content-Type"))
data = r.read()
print("got bytes:", len(data))
off = 0
atoms = []
while off + 8 <= len(data):
    size = struct.unpack(">I", data[off:off + 4])[0]
    typ = data[off + 4:off + 8].decode("latin1")
    atoms.append((typ, size))
    if size < 8:
        break
    off += size
    if len(atoms) > 20:
        break
print("top atoms:", atoms)
