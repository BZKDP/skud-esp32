# -*- coding: utf-8 -*-
import s3lite, subprocess
keys = [o["key"] for o in s3lite.list_objects("rec/outdoor/2026-07-04") if o["key"].endswith(".mp4")]
key = keys[0]
url = s3lite.presign_get(key, 600)
print("key:", key)
ff = r"C:\ProgramData\chocolatey\bin\ffprobe.exe"
out = subprocess.run([ff, "-v", "error", "-show_entries",
                      "stream=codec_name,codec_type,width,height,pix_fmt,profile:format=duration,format_name",
                      "-of", "default", url], capture_output=True, text=True, timeout=90)
print("RC", out.returncode)
print("STDOUT:\n" + out.stdout)
print("STDERR:\n" + out.stderr[:1000])
