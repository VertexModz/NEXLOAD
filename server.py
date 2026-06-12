#!/usr/bin/env python3
"""
💕 Love Quiz Server
- Termux : python3 server.py
- Railway : อ่าน PORT จาก environment variable อัตโนมัติ
"""

import http.server
import socketserver
import socket
import sys
import os

# Railway inject PORT ผ่าน env — ถ้าไม่มีใช้ 8888
PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8888))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        print(f"  📱 {self.address_string()} → {format % args}", flush=True)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def main():
    # Railway ต้องการ bind 0.0.0.0
    HOST = "0.0.0.0"

    # TCPServer ต้อง allow_reuse_address ก่อน bind
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        is_railway = "RAILWAY_ENVIRONMENT" in os.environ or "RAILWAY_PUBLIC_DOMAIN" in os.environ
        public_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

        print()
        print("  💕 ══════════════════════════════════ 💕")
        print("       Love Quiz Server กำลังทำงานอยู่!")
        print("  💕 ══════════════════════════════════ 💕")
        print()
        if is_railway and public_domain:
            print(f"  🌍 Railway URL : https://{public_domain}")
        else:
            local_ip = get_local_ip()
            print(f"  🏠 เปิดในเครื่องนี้ : http://localhost:{PORT}")
            print(f"  📡 แชร์ใน Wi-Fi    : http://{local_ip}:{PORT}")
        print(f"  🔌 Listening on {HOST}:{PORT}")
        print()
        print("  🛑 กด Ctrl+C เพื่อหยุดเซิร์ฟเวอร์")
        print()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  💔 ปิดเซิร์ฟเวอร์แล้ว บาย~")

if __name__ == "__main__":
    main()
