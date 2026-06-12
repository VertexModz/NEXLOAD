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
import json
import urllib.parse

PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8888))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(DIRECTORY, "questions.json")

# Default questions (ใช้เมื่อยังไม่มีไฟล์)
DEFAULT_QUESTIONS = [
    {"type":"yesno","emoji":"🌙","text":"ถ้าดึกมากแล้วและหิวข้าว จะตื่นมาทำข้าวให้อีกคนไหม?","correct":"yes","feedback":{"yes":"โอ้โห! ดูแลกันดีมากเลย 🥺💕","no":"ก็ได้นะ... แต่ถ้าได้รับอาหารดึกๆ มันพิเศษมากเลย 🍜"}},
    {"type":"choice","emoji":"🎬","text":"ถ้าจะดูหนังด้วยกันคืนนี้ อยากดูแนวไหนมากที่สุด?","choices":["หนังรักโรแมนติก 🌹","หนังตลก ฮาๆ 😂","ดูซีรีส์เกาหลีด้วยกัน 🇰🇷","แอนิเมชันน่ารักๆ 🐰"],"correct":None,"feedback":"ไม่ว่าจะเลือกอะไร ขอแค่ได้นอนดูด้วยกันก็พอ 🛋️💕"},
    {"type":"text","emoji":"💌","text":"บอกสิ่งที่รักในตัวอีกคนมาสักอย่างได้เลย~","feedback":"ขอบคุณที่บอกนะ มันทำให้รู้สึกอบอุ่นมากเลย 🥰"},
    {"type":"yesno","emoji":"🌧️","text":"วันที่ฝนตก ถ้าไม่มีร่ม จะแบ่งร่มให้อีกคน แม้ตัวเองจะเปียก?","correct":"yes","feedback":{"yes":"นั่นคือความรักแบบ k-drama จริงๆ 💕🌂","no":"ซื่อสัตย์ดี! แต่เปียกด้วยกันก็ไม่เป็นไร 😄"}}
]

def load_questions():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_QUESTIONS[:]

def save_questions(questions):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        print(f"  📱 {self.address_string()} → {format % args}", flush=True)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/questions":
            self.send_json(load_questions())
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/questions":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                questions = json.loads(body)
                if not isinstance(questions, list):
                    self.send_json({"error": "expected array"}, 400)
                    return
                save_questions(questions)
                self.send_json({"ok": True, "count": len(questions)})
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
        else:
            self.send_json({"error": "not found"}, 404)

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
    HOST = "0.0.0.0"
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
        print(f"  📂 ข้อมูลคำถาม : {DATA_FILE}")
        print()
        print("  🛑 กด Ctrl+C เพื่อหยุดเซิร์ฟเวอร์")
        print()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  💔 ปิดเซิร์ฟเวอร์แล้ว บาย~")

if __name__ == "__main__":
    main()
