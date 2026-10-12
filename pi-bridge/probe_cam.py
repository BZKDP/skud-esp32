import os, socket, base64, time, sys

HOST = os.getenv('CAM_HOST', '<CAMERA_IP>'); PORT = 554
PATH = sys.argv[1] if len(sys.argv) > 1 else '/Streaming/Channels/102'
url = f'rtsp://{HOST}:{PORT}{PATH}'
a = base64.b64encode(f"{os.getenv('CAM_USER','admin')}:{os.getenv('CAM_PASSWORD','')}".encode()).decode()

s = socket.socket(); s.settimeout(6); s.connect((HOST, PORT))

def req(method, cseq, auth=False):
    h = f'{method} {url} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: probe\r\n'
    if method == 'DESCRIBE':
        h += 'Accept: application/sdp\r\n'
    if auth:
        h += f'Authorization: Basic {a}\r\n'
    h += '\r\n'
    s.send(h.encode())
    time.sleep(1.2)
    try:
        data = s.recv(8192)
    except Exception as e:
        data = b'<recv error: ' + str(e).encode() + b'>'
    print(f'==== {method} auth={auth} path={PATH} ====')
    print(repr(data))
    print()

req('OPTIONS', 1)
req('DESCRIBE', 2, auth=False)
req('DESCRIBE', 3, auth=True)
s.close()
