import serial, time

port = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"  # CH340 = дверь
s = serial.Serial(port, 115200, timeout=1)
time.sleep(2)
s.reset_input_buffer()
for cmd in [b"open 1500\n", b"open2 1500\n", b"open3 1500\n", b"open4 1500\n"]:
    s.write(cmd)
    time.sleep(2.2)
    resp = s.read(400)
    # печатаем только строки про открытие/реле, без wiegand-мусора
    lines = [l for l in resp.split(b"\r\n") if b"\xd0" in l and b"Reader" not in l and b"Wiegand" not in l]
    print(cmd.strip().decode(), "->", b" | ".join(lines).decode("utf-8", "replace"))
s.close()
print("DONE")
