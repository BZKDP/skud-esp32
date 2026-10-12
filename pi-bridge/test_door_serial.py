import serial, time

port = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"  # CH340 = дверь
s = serial.Serial(port, 115200, timeout=1)
time.sleep(2)                      # ESP32 ресетится при открытии порта — ждём загрузку
print("BANNER:", s.read(400))
s.reset_input_buffer()
s.write(b"ip\n"); time.sleep(0.6)
print("RESP ip  :", s.read(300))
s.write(b"open\n"); time.sleep(1.8)  # должно щёлкнуть реле двери 1
print("RESP open:", s.read(300))
s.close()
print("DONE")
