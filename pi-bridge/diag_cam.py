import os, socket, base64, time, urllib.request

HOST = os.getenv('CAM_HOST', '<CAMERA_IP>')
a = base64.b64encode(f"{os.getenv('CAM_USER','admin')}:{os.getenv('CAM_PASSWORD','')}".encode()).decode()

# 1) HTTP с Basic — проверяем валидность учётки (порт 80, не трогает RTSP-флуд)
print('==== HTTP GET / (Basic) ====')
try:
    req = urllib.request.Request(f'http://{HOST}/', headers={'Authorization': f'Basic {a}'})
    with urllib.request.urlopen(req, timeout=6) as r:
        print('HTTP', r.status, '| первые 120 байт:', r.read(120))
except Exception as e:
    print('HTTP error:', repr(e))

time.sleep(2)

# 2) Свежее соединение, DESCRIBE с Basic ПЕРВЫМ запросом (без предварительного 401)
print('==== RTSP DESCRIBE (Basic, свежий сокет, первый запрос) ====')
s = socket.socket(); s.settimeout(8); s.connect((HOST, 554))
h = ('DESCRIBE rtsp://%s:554/Streaming/Channels/102 RTSP/1.0\r\n'
     'CSeq: 1\r\nAccept: application/sdp\r\n'
     'Authorization: Basic %s\r\nUser-Agent: Lavf\r\n\r\n') % (HOST, a)
s.send(h.encode())
time.sleep(1.5)
buf = b''
try:
    while True:
        chunk = s.recv(8192)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 16000:
            break
except Exception as e:
    pass
print(repr(buf[:1200]))
s.close()
