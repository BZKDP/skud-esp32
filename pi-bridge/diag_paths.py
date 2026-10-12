import os, socket, base64, time

HOST = os.getenv('CAM_HOST', '<CAMERA_IP>')
a = base64.b64encode(f"{os.getenv('CAM_USER','admin')}:{os.getenv('CAM_PASSWORD','')}".encode()).decode()
paths = [
    '/11', '/12',
    '/h264/ch1/main/av_stream', '/h264/ch1/sub/av_stream',
    '/Streaming/Channels/101', '/cam/realmonitor?channel=1&subtype=1',
]

def describe(path):
    url = 'rtsp://%s:554%s' % (HOST, path)
    s = socket.socket(); s.settimeout(7)
    try:
        s.connect((HOST, 554))
    except Exception as e:
        return 'connect error: %r' % e
    h = ('DESCRIBE %s RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n'
         'Authorization: Basic %s\r\nUser-Agent: Lavf\r\n\r\n') % (url, a)
    s.send(h.encode())
    time.sleep(1.3)
    buf = b''
    try:
        while True:
            c = s.recv(8192)
            if not c:
                break
            buf += c
            if len(buf) > 4000:
                break
    except Exception:
        pass
    s.close()
    return buf[:500]

for i, p in enumerate(paths):
    print('==== %s ====' % p)
    print(repr(describe(p)))
    print()
    if i < len(paths) - 1:
        time.sleep(18)
