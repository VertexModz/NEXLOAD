"""
╔══════════════════════════════════════════════════════╗
║   ▓▓▓  NEXLOAD WEB — YouTube Downloader Web UI  ▓▓▓  ║
║         ปรับปรุงใหม่ v5.1 (ภาษาไทยทั้งหมด)            ║
╚══════════════════════════════════════════════════════╝

  ติดตั้ง:
    1 : pip install flask
    2 : pip install yt-dlp
    3 : pkg install ffmpeg
    4 : pkg upgrade

  รัน:
    python nexload_web.py

  เปิดใน Chrome:
    http://localhost:8888
"""

from flask import Flask, request, jsonify, Response, send_file
import yt_dlp, os, re, json, time, threading, shutil, queue, requests as req_lib, subprocess, sys, logging
from datetime import datetime

# ══════════════════════════════════════════════
#  Logging
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("nexload.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("nexload")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# ══════════════════════════════════════════════
#  ไฟล์ข้อมูล
# ══════════════════════════════════════════════
CONFIG_FILE  = "nexload_config.json"
HISTORY_FILE = "nexload_history.json"
QUEUE_FILE   = "nexload_queue.json"
STATS_FILE   = "nexload_stats.json"

DEFAULT_CONFIG = {
    "output_dir": "nexload_video", "max_filename": 180,
    "ext": "mp4", "subtitles": False, "sub_langs": "th,en",
    "thumbnail": False, "playlist": False,
    "speed_limit": "",
    "audio_only": False, "audio_format": "mp3", "audio_quality": "192",
    "auto_retry": True, "retry_count": 3, "retry_wait": 5,
    "resolution": "best",
    "webhook_url": "", "webhook_type": "none",
}

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: pass
    return default() if callable(default) else default

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def load_cfg():
    saved = load_json(CONFIG_FILE, {})
    cfg = DEFAULT_CONFIG.copy(); cfg.update(saved); return cfg

def save_cfg(cfg): save_json(CONFIG_FILE, cfg)
def load_history(): return load_json(HISTORY_FILE, [])
def save_history(h): save_json(HISTORY_FILE, h[-200:])
def load_queue(): return load_json(QUEUE_FILE, [])
def save_queue(q): save_json(QUEUE_FILE, q)

def load_stats():
    d = load_json(STATS_FILE, None)
    if d: return d
    s = {"total_downloads":0,"total_bytes":0,"total_seconds":0,"failed":0,"first_use":""}
    s["first_use"] = datetime.now().strftime("%Y-%m-%d"); return s

def save_stats(s): save_json(STATS_FILE, s)

# ══════════════════════════════════════════════
#  ฟังก์ชันช่วย
# ══════════════════════════════════════════════
def fmt_dur(sec):
    if not sec: return "--:--"
    h,r = divmod(int(sec),3600); m,s = divmod(r,60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def fmt_size(b):
    if not b: return "0 B"
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def safe_fn(title, max_bytes=180):
    title = re.sub(r'[\\/*?:"<>|]', '', title).strip()
    enc = title.encode("utf-8")
    if len(enc) > max_bytes: title = enc[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return title

def res_to_format(res):
    if res == "best": return "bestvideo+bestaudio/best"
    return (f"bestvideo[height<={res}]+bestaudio/best[height<={res}]/best")

# ══════════════════════════════════════════════
#  Webhook แจ้งเตือน
# ══════════════════════════════════════════════
def send_webhook(title, filename, size, elapsed, success=True):
    cfg = load_cfg()
    url = cfg.get("webhook_url","").strip()
    wtype = cfg.get("webhook_type","none")
    if not url or wtype == "none": return
    status = "✅ สำเร็จ" if success else "❌ ล้มเหลว"
    msg = f"{status} | {title}\nไฟล์: {filename} | ขนาด: {size} | เวลา: {elapsed}"
    try:
        if wtype == "discord":
            req_lib.post(url, json={"content": msg}, timeout=5)
        elif wtype == "line":
            req_lib.post("https://notify-api.line.me/api/notify",
                         headers={"Authorization": f"Bearer {url}"},
                         data={"message": msg}, timeout=5)
        elif wtype == "telegram":
            # url format: botTOKEN|chatID
            parts = url.split("|")
            if len(parts) == 2:
                req_lib.post(f"https://api.telegram.org/bot{parts[0]}/sendMessage",
                             json={"chat_id": parts[1], "text": msg}, timeout=5)
    except: pass

# ══════════════════════════════════════════════
#  เครื่องยนต์ดาวน์โหลด (SSE progress)
# ══════════════════════════════════════════════
_progress_q = queue.Queue()
_cancel_flag = threading.Event()   # set() = ยกเลิก, clear() = ปกติ

def _sse_msg(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

# ══════════════════════════════════════════════
#  ตรวจสอบพื้นที่ดิสก์
# ══════════════════════════════════════════════
def check_disk_space(folder, need_bytes=0):
    """คืน (free_bytes, enough) — enough=False ถ้าพื้นที่ไม่พอ (ต้องการ 200 MB บัฟเฟอร์)"""
    try:
        stat = shutil.disk_usage(folder)
        buffer = 200 * 1024 * 1024   # 200 MB safety buffer
        return stat.free, stat.free >= (need_bytes + buffer)
    except Exception as e:
        log.warning(f"disk_usage error: {e}")
        return 0, True   # ถ้าเช็กไม่ได้ให้ผ่าน

def build_opts(cfg, out_tmpl, hooks=None):
    opts = {"outtmpl": out_tmpl, "quiet": True, "no_warnings": True,
            "noplaylist": not cfg["playlist"]}
    if hooks: opts["progress_hooks"] = hooks
    if cfg["audio_only"]:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key":"FFmpegExtractAudio",
                                    "preferredcodec":cfg["audio_format"],
                                    "preferredquality":cfg["audio_quality"]}]
    else:
        opts["format"] = res_to_format(cfg["resolution"])
        opts["merge_output_format"] = cfg["ext"]
        opts["postprocessors"] = [{"key":"FFmpegVideoConvertor","preferedformat":cfg["ext"]}]
    if cfg["subtitles"]:
        opts["writesubtitles"] = True; opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = cfg["sub_langs"].split(",")
    if cfg["thumbnail"]: opts["writethumbnail"] = True
    if cfg["speed_limit"]: opts["ratelimit"] = cfg["speed_limit"]
    if os.path.exists("cookies.txt"): opts["cookiefile"] = "cookies.txt"
    return opts

def _make_hook():
    last = ["-1"]
    def hook(d):
        if _cancel_flag.is_set():
            raise yt_dlp.utils.DownloadCancelled("ยกเลิกโดยผู้ใช้")
        if d["status"] == "downloading":
            dl = d.get("downloaded_bytes", 0) or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total > 0: pct = min(int(dl/total*100), 100)
            else:
                try: pct = min(int(float(d.get("_percent_str","0%").replace("%",""))), 100)
                except: pct = 0
            if str(pct) == last[0]: return
            last[0] = str(pct)
            _progress_q.put({"type":"progress","pct":pct,
                "speed":d.get("_speed_str","").strip() or "---",
                "eta":d.get("_eta_str","").strip() or "---",
                "dl":fmt_size(dl),"total":fmt_size(total)})
        elif d["status"] == "finished":
            _progress_q.put({"type":"merging"})
    return hook

def _run_download(url, cfg, title):
    _cancel_flag.clear()
    _progress_q.put({"type":"start","title":title})
    log.info(f"เริ่มดาวน์โหลด: {title} | {url}")
    os.makedirs(cfg["output_dir"], exist_ok=True)

    # ── ตรวจสอบพื้นที่ดิสก์ก่อนเริ่ม ──
    free, enough = check_disk_space(cfg["output_dir"])
    if not enough:
        msg = f"พื้นที่ดิสก์ไม่เพียงพอ (เหลือ {fmt_size(free)}) ต้องการอย่างน้อย 200 MB"
        log.warning(msg)
        _progress_q.put({"type":"error","msg":msg})
        return

    safe_t = safe_fn(title, cfg["max_filename"])
    out_tmpl = os.path.join(cfg["output_dir"], f"{safe_t}.%(ext)s")
    opts = build_opts(cfg, out_tmpl, [_make_hook()])
    ext = cfg["audio_format"] if cfg["audio_only"] else cfg["ext"]
    attempts = cfg["retry_count"] if cfg["auto_retry"] else 1
    t_start = time.time()
    for attempt in range(1, attempts+1):
        if _cancel_flag.is_set():
            log.info(f"ยกเลิกดาวน์โหลด: {title}")
            _progress_q.put({"type":"cancelled"})
            return
        try:
            with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
            elapsed = time.time() - t_start
            size = 0
            fp = os.path.join(cfg["output_dir"], f"{safe_t}.{ext}")
            if os.path.exists(fp): size = os.path.getsize(fp)
            s = load_stats()
            if not s["first_use"]: s["first_use"] = datetime.now().strftime("%Y-%m-%d")
            s["total_downloads"] += 1; s["total_bytes"] += size; s["total_seconds"] += elapsed
            save_stats(s)
            h = load_history()
            h.append({"title":title,"url":url,"ext":ext,"size":size,
                       "elapsed":round(elapsed,1),"status":"✔",
                       "date":datetime.now().strftime("%Y-%m-%d %H:%M")})
            save_history(h)
            size_str = fmt_size(size)
            elapsed_str = f"{elapsed:.1f}s"
            log.info(f"ดาวน์โหลดสำเร็จ: {title} | {size_str} | {elapsed_str}")
            _progress_q.put({"type":"done","title":title,"file":f"{safe_t}.{ext}",
                              "size":size_str,"elapsed":elapsed_str})
            send_webhook(title, f"{safe_t}.{ext}", size_str, elapsed_str, success=True)
            return
        except yt_dlp.utils.DownloadCancelled:
            log.info(f"ยกเลิกดาวน์โหลด: {title}")
            _progress_q.put({"type":"cancelled"})
            # ลบไฟล์ค้างถ้ามี
            for f in os.listdir(cfg["output_dir"]):
                if f.startswith(safe_t) and f.endswith(".part"):
                    try: os.remove(os.path.join(cfg["output_dir"], f))
                    except: pass
            return
        except Exception as e:
            log.error(f"ดาวน์โหลดล้มเหลว (ครั้งที่ {attempt}): {title} | {e}")
            if attempt < attempts:
                _progress_q.put({"type":"retry","attempt":attempt,"wait":cfg["retry_wait"]})
                time.sleep(cfg["retry_wait"])
            else:
                s = load_stats(); s["failed"] += 1; save_stats(s)
                h = load_history()
                h.append({"title":title,"url":url,"ext":ext,"size":0,
                           "elapsed":round(time.time()-t_start,1),"status":"✖",
                           "date":datetime.now().strftime("%Y-%m-%d %H:%M")})
                save_history(h)
                _progress_q.put({"type":"error","msg":str(e)})
                send_webhook(title, "-", "0 B", "0s", success=False)

# ══════════════════════════════════════════════
#  Auto Queue Worker
# ══════════════════════════════════════════════
_auto_queue_lock = threading.Lock()
_auto_queue_status = {"running": False, "current": "", "done": 0, "total": 0}

def _auto_queue_worker():
    global _progress_q
    cfg = load_cfg()
    q = load_queue()
    total = len(q)
    _auto_queue_status.update({"running": True, "done": 0, "total": total})
    for idx, item in enumerate(q):
        _auto_queue_status["current"] = item.get("title","")
        _auto_queue_status["done"] = idx
        _progress_q = queue.Queue()
        _run_download(item["url"], cfg, item.get("title","video"))
        remaining = load_queue()
        if remaining:
            remaining.pop(0)
            save_queue(remaining)
        time.sleep(1)
    _auto_queue_status.update({"running": False, "current": "", "done": total, "total": total})
    _progress_q.put({"type":"queue_done","total":total})

# ══════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════
@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.json; url = data.get("url","").strip()
    if not url: return jsonify({"error":"ต้องระบุ URL"}), 400
    cfg = load_cfg()
    try:
        opts = {"quiet":True,"no_warnings":True,"noplaylist":True}
        if cfg.get("proxy"): opts["proxy"] = cfg["proxy"]
        if os.path.exists("cookies.txt"): opts["cookiefile"] = "cookies.txt"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        fmts = info.get("formats",[])
        best_sz = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in fmts), default=0)
        heights = sorted(set(
            f.get("height") for f in fmts
            if f.get("height") and f.get("vcodec","none") != "none"
        ), reverse=True)
        return jsonify({
            "title": info.get("title",""),
            "channel": info.get("uploader",""),
            "duration": fmt_dur(info.get("duration")),
            "views": f"{info.get('view_count',0):,}",
            "size": fmt_size(best_sz),
            "thumbnail": info.get("thumbnail",""),
            "resolutions": heights,
            "is_playlist": info.get("_type") == "playlist",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.json; kw = data.get("q","").strip()
    if not kw: return jsonify({"error":"ต้องระบุคำค้นหา"}), 400
    cfg = load_cfg()
    try:
        opts = {"quiet":True,"no_warnings":True,"extract_flat":True}
        if os.path.exists("cookies.txt"): opts["cookiefile"] = "cookies.txt"
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch10:{kw}", download=False)
        entries = []
        for e in (res.get("entries") or []):
            entries.append({
                "title": e.get("title",""),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id','')}",
                "duration": fmt_dur(e.get("duration")),
                "views": f"{e.get('view_count',0):,}",
                "channel": e.get("uploader",""),
                "thumbnail": e.get("thumbnail",""),
            })
        return jsonify({"results": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/download", methods=["POST"])
def api_download():
    global _progress_q
    data = request.json
    url = data.get("url","").strip()
    title = data.get("title","video")
    cfg = load_cfg()
    if data.get("resolution"): cfg["resolution"] = data["resolution"]
    if data.get("audio_only") is not None: cfg["audio_only"] = data["audio_only"]
    _progress_q = queue.Queue()
    t = threading.Thread(target=_run_download, args=(url, cfg, title), daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/progress")
def api_progress():
    def generate():
        yield _sse_msg({"type":"connected"})
        while True:
            try:
                msg = _progress_q.get(timeout=30)
                yield _sse_msg(msg)
                if msg["type"] in ("done","error","queue_done"): break
            except queue.Empty:
                yield _sse_msg({"type":"ping"})
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    _cancel_flag.set()
    log.info("ผู้ใช้กดยกเลิกดาวน์โหลด")
    return jsonify({"ok": True})

@app.route("/api/diskspace")
def api_diskspace():
    cfg = load_cfg()
    folder = cfg["output_dir"]
    os.makedirs(folder, exist_ok=True)
    try:
        stat = shutil.disk_usage(folder)
        return jsonify({
            "free": stat.free,
            "total": stat.total,
            "used": stat.used,
            "free_str": fmt_size(stat.free),
            "total_str": fmt_size(stat.total),
            "pct_used": int(stat.used / stat.total * 100)
        })
    except Exception as e:
        log.error(f"diskspace error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/update-ytdlp", methods=["POST"])
def api_update_ytdlp():
    def _do_update():
        try:
            log.info("เริ่ม update yt-dlp...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                log.info("update yt-dlp สำเร็จ")
                _progress_q.put({"type":"ytdlp_updated","ok":True,"msg":"อัปเดต yt-dlp สำเร็จ ✔"})
            else:
                log.error(f"update yt-dlp ล้มเหลว: {result.stderr}")
                _progress_q.put({"type":"ytdlp_updated","ok":False,"msg":result.stderr[:200]})
        except Exception as e:
            log.error(f"update yt-dlp exception: {e}")
            _progress_q.put({"type":"ytdlp_updated","ok":False,"msg":str(e)})
    threading.Thread(target=_do_update, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/ytdlp-version")
def api_ytdlp_version():
    try:
        import yt_dlp.version as v
        return jsonify({"version": v.__version__})
    except Exception as e:
        return jsonify({"version": "unknown", "error": str(e)})


@app.route("/api/queue", methods=["GET"])
def api_queue_get():
    return jsonify(load_queue())

@app.route("/api/queue", methods=["POST"])
def api_queue_add():
    data = request.json
    items = data.get("items")  # รองรับ multi-URL
    q = load_queue()
    if items:
        for it in items:
            q.append({"url": it.get("url",""), "title": it.get("title","")})
    else:
        q.append({"url": data.get("url",""), "title": data.get("title","")})
    save_queue(q)
    return jsonify({"ok":True,"count":len(q)})

@app.route("/api/queue/<int:idx>", methods=["DELETE"])
def api_queue_del(idx):
    q = load_queue()
    if 0 <= idx < len(q): q.pop(idx); save_queue(q)
    return jsonify({"ok":True})

@app.route("/api/queue/clear", methods=["DELETE"])
def api_queue_clear():
    save_queue([])
    return jsonify({"ok":True})

@app.route("/api/queue/run", methods=["POST"])
def api_queue_run():
    if _auto_queue_status["running"]:
        return jsonify({"error":"คิวกำลังทำงานอยู่"}), 400
    q = load_queue()
    if not q:
        return jsonify({"error":"คิวว่างเปล่า"}), 400
    t = threading.Thread(target=_auto_queue_worker, daemon=True)
    t.start()
    return jsonify({"ok":True,"total":len(q)})

@app.route("/api/queue/status")
def api_queue_status():
    return jsonify(_auto_queue_status)

@app.route("/api/files/<path:name>/download")
def api_file_download(name):
    cfg = load_cfg()
    fp = os.path.join(cfg["output_dir"], name)
    if not os.path.exists(fp):
        return jsonify({"error":"ไม่พบไฟล์"}), 404
    return send_file(fp, as_attachment=True, download_name=name)

# ══════════════════════════════════════════════
#  HTML หลัก
# ══════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXLOAD — ดาวน์โหลด YouTube</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&family=Noto+Sans+Thai:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #020408;
  --bg2: #060d14;
  --bg3: #0a1520;
  --cy: #00f5ff;
  --mg: #ff00cc;
  --gr: #00ff88;
  --yl: #ffcc00;
  --rd: #ff3355;
  --dim: #1a2a3a;
  --txt: #c8e8ff;
  --glow-cy: 0 0 20px #00f5ff88, 0 0 40px #00f5ff33;
  --glow-mg: 0 0 20px #ff00cc88, 0 0 40px #ff00cc33;
  --glow-gr: 0 0 20px #00ff8888, 0 0 40px #00ff8833;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:var(--bg);
  color:var(--txt);
  font-family:'Noto Sans Thai','Share Tech Mono',monospace;
  min-height:100vh;
  overflow-x:hidden;
}
body::before{
  content:'';position:fixed;inset:0;z-index:0;
  background-image:
    linear-gradient(rgba(0,245,255,.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(0,245,255,.03) 1px,transparent 1px);
  background-size:40px 40px;pointer-events:none;
}
body::after{
  content:'';position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse 80% 50% at 50% -10%,rgba(0,245,255,.08),transparent);
  pointer-events:none;
}
.scanlines{
  position:fixed;inset:0;z-index:1;pointer-events:none;
  background:repeating-linear-gradient(transparent,transparent 2px,rgba(0,0,0,.15) 2px,rgba(0,0,0,.15) 4px);
  animation:scan 8s linear infinite;
}
@keyframes scan{from{background-position:0 0}to{background-position:0 100vh}}
#app{position:relative;z-index:2;max-width:960px;margin:0 auto;padding:16px}

/* HEADER */
.header{text-align:center;padding:32px 0 24px;position:relative}
.logo{
  font-family:'Orbitron',sans-serif;
  font-size:clamp(2rem,6vw,3.5rem);font-weight:900;letter-spacing:.12em;
  background:linear-gradient(135deg,var(--cy) 0%,var(--mg) 50%,var(--cy) 100%);
  background-size:200%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;
  animation:shimmer 3s linear infinite,logoIn .8s ease both;
  filter:drop-shadow(0 0 20px #00f5ff66);
}
@keyframes shimmer{from{background-position:200% center}to{background-position:-200% center}}
@keyframes logoIn{from{opacity:0;transform:translateY(-30px) scale(.9)}to{opacity:1;transform:none}}
.logo-sub{font-family:'Share Tech Mono',monospace;font-size:.75rem;color:#4a7a9a;letter-spacing:.3em;margin-top:4px;animation:fadeIn .8s .3s both}
.sys-time{font-family:'Share Tech Mono',monospace;font-size:.7rem;color:#2a5a7a;margin-top:8px;animation:fadeIn .8s .5s both}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.corner-tl,.corner-tr,.corner-bl,.corner-br{position:absolute;width:20px;height:20px;border-color:var(--cy);border-style:solid;opacity:.4}
.corner-tl{top:8px;left:8px;border-width:2px 0 0 2px}
.corner-tr{top:8px;right:8px;border-width:2px 2px 0 0}
.corner-bl{bottom:8px;left:8px;border-width:0 0 2px 2px}
.corner-br{bottom:8px;right:8px;border-width:0 2px 2px 0}

/* NAV */
.nav{display:flex;gap:4px;background:var(--bg2);border:1px solid #0a2535;border-radius:8px;padding:4px;margin-bottom:20px;overflow-x:auto;scrollbar-width:none;animation:fadeIn .6s .4s both}
.nav::-webkit-scrollbar{display:none}
.nav-btn{flex:1;min-width:72px;padding:10px 8px;background:transparent;border:none;border-radius:6px;color:#4a7a9a;font-family:'Noto Sans Thai',sans-serif;font-size:.65rem;font-weight:700;cursor:pointer;transition:all .2s;white-space:nowrap}
.nav-btn:hover{color:var(--cy);background:rgba(0,245,255,.05)}
.nav-btn.active{color:var(--bg);background:linear-gradient(135deg,var(--cy),var(--mg));box-shadow:var(--glow-cy)}
.nav-icon{font-size:1rem;display:block;margin-bottom:2px}

/* PANEL */
.panel{display:none;animation:panelIn .35s ease both}
.panel.active{display:block}
@keyframes panelIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}

/* CARD */
.card{background:var(--bg2);border:1px solid #0d2030;border-radius:12px;padding:20px;margin-bottom:16px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--cy),transparent);animation:lineScan 3s linear infinite}
@keyframes lineScan{from{opacity:0}50%{opacity:.6}to{opacity:0}}
.section-title{font-family:'Orbitron',sans-serif;font-size:.7rem;font-weight:700;color:var(--cy);letter-spacing:.15em;text-transform:uppercase;display:flex;align-items:center;gap:8px;margin-bottom:16px}
.section-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(0,245,255,.3),transparent)}

/* INPUT */
.input-wrap{display:flex;gap:8px;flex-wrap:wrap}
.url-input{flex:1;min-width:200px;background:#030a12;border:1px solid #1a3a55;border-radius:8px;color:var(--txt);font-family:'Share Tech Mono',monospace;font-size:.85rem;padding:12px 16px;outline:none;transition:border-color .2s,box-shadow .2s}
.url-input:focus{border-color:var(--cy);box-shadow:0 0 0 2px rgba(0,245,255,.15)}
.url-input::placeholder{color:#2a4a6a}
textarea.url-input{resize:vertical;min-height:80px;font-size:.78rem;line-height:1.6}

/* BUTTONS */
.btn{padding:12px 20px;border:none;border-radius:8px;font-family:'Noto Sans Thai',sans-serif;font-size:.72rem;font-weight:700;cursor:pointer;transition:all .2s;white-space:nowrap;position:relative;overflow:hidden}
.btn-cy{background:linear-gradient(135deg,#006680,#009ab5);color:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.1)}
.btn-cy:hover{background:linear-gradient(135deg,#0088aa,#00c5e0);box-shadow:var(--glow-cy)}
.btn-mg{background:linear-gradient(135deg,#660055,#aa0088);color:#fff}
.btn-mg:hover{background:linear-gradient(135deg,#880077,#cc00aa);box-shadow:var(--glow-mg)}
.btn-gr{background:linear-gradient(135deg,#006640,#00aa66);color:#fff}
.btn-gr:hover{background:linear-gradient(135deg,#008855,#00cc77);box-shadow:var(--glow-gr)}
.btn-rd{background:linear-gradient(135deg,#660022,#aa0033);color:#fff}
.btn-rd:hover{background:linear-gradient(135deg,#880033,#cc0044)}
.btn-dim{background:#0a1a28;color:#4a7a9a;border:1px solid #1a3a55}
.btn-dim:hover{border-color:var(--cy);color:var(--cy)}
.btn:disabled{opacity:.4;cursor:not-allowed}

/* VIDEO INFO */
.video-info{display:none;background:#030a12;border:1px solid #1a3a55;border-radius:10px;padding:16px;margin-top:16px;gap:16px;animation:fadeIn .3s ease}
.video-info.show{display:flex;flex-wrap:wrap}
.thumb-wrap{position:relative;flex-shrink:0}
.thumb{width:160px;height:90px;object-fit:cover;border-radius:6px;border:1px solid #1a3a55;display:block}
.thumb-overlay{position:absolute;inset:0;border-radius:6px;background:linear-gradient(135deg,rgba(0,245,255,.1),rgba(255,0,204,.1));border:1px solid rgba(0,245,255,.2)}
.video-meta{flex:1;min-width:180px}
.video-title{font-size:.9rem;font-weight:600;color:#fff;margin-bottom:8px;line-height:1.4}
.video-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.tag{font-family:'Share Tech Mono',monospace;font-size:.65rem;padding:3px 8px;border-radius:4px;background:rgba(0,245,255,.08);border:1px solid rgba(0,245,255,.2);color:var(--cy)}
.tag.mg{background:rgba(255,0,204,.08);border-color:rgba(255,0,204,.2);color:var(--mg)}
.tag.gr{background:rgba(0,255,136,.08);border-color:rgba(0,255,136,.2);color:var(--gr)}
.dl-opts{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px;align-items:center}
.select-box{background:#030a12;border:1px solid #1a3a55;border-radius:6px;color:var(--txt);font-family:'Noto Sans Thai',sans-serif;font-size:.75rem;padding:8px 12px;outline:none;cursor:pointer;transition:border-color .2s}
.select-box:focus{border-color:var(--cy)}
.toggle-wrap{display:flex;align-items:center;gap:8px;font-family:'Noto Sans Thai',sans-serif;font-size:.75rem;color:#4a7a9a}
.toggle{width:36px;height:20px;background:#0a1a28;border:1px solid #1a3a55;border-radius:10px;position:relative;cursor:pointer;transition:.2s}
.toggle.on{background:linear-gradient(135deg,#006680,#00c5e0);border-color:var(--cy);box-shadow:0 0 8px rgba(0,245,255,.4)}
.toggle::after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:.2s}
.toggle.on::after{left:18px}

/* PROGRESS */
.progress-wrap{display:none;background:#030a12;border:1px solid #1a3a55;border-radius:10px;padding:20px;margin-top:16px;animation:fadeIn .3s}
.progress-wrap.show{display:block}
.progress-title{font-family:'Orbitron',sans-serif;font-size:.65rem;color:var(--cy);letter-spacing:.15em;margin-bottom:12px}
.progress-text{font-family:'Noto Sans Thai',sans-serif;font-size:.8rem;color:#aaa;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-wrap{height:8px;background:#0a1a28;border-radius:4px;overflow:hidden;border:1px solid #1a3a55;margin-bottom:10px;position:relative}
.bar-fill{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--cy),var(--mg));transition:width .3s;position:relative;box-shadow:0 0 8px rgba(0,245,255,.5)}
.bar-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:4px;background:rgba(255,255,255,.8);border-radius:2px;animation:pulse .5s ease-in-out infinite alternate}
@keyframes pulse{from{opacity:.5}to{opacity:1}}
.progress-stats{display:flex;gap:16px;flex-wrap:wrap;font-family:'Share Tech Mono',monospace;font-size:.7rem}
.stat-item{display:flex;flex-direction:column;gap:2px}
.stat-label{color:#2a5a7a;font-size:.6rem;letter-spacing:.1em}
.stat-val{color:var(--cy)}
.done-banner{display:none;background:linear-gradient(135deg,rgba(0,255,136,.1),rgba(0,245,255,.05));border:1px solid rgba(0,255,136,.3);border-radius:10px;padding:16px 20px;margin-top:12px;animation:fadeIn .4s}
.done-banner.show{display:flex;align-items:center;gap:12px}
.done-icon{font-size:1.5rem;animation:pop .4s ease}
@keyframes pop{from{transform:scale(0)}to{transform:scale(1)}}
.done-details{font-family:'Share Tech Mono',monospace;font-size:.75rem;color:#aaa}
.done-details strong{color:var(--gr);display:block;font-size:.85rem;margin-bottom:4px}
.err-banner{display:none;background:rgba(255,51,85,.08);border:1px solid rgba(255,51,85,.3);border-radius:10px;padding:14px 16px;margin-top:12px;animation:fadeIn .3s;font-family:'Share Tech Mono',monospace;font-size:.75rem;color:#ff8899}
.err-banner.show{display:block}

/* TYPEWRITER */
.typewriter{font-family:'Share Tech Mono',monospace;font-size:.75rem;color:#4a9a9a;border-right:2px solid var(--cy);padding-right:2px;animation:blink .7s step-end infinite;white-space:nowrap;overflow:hidden}
@keyframes blink{50%{border-color:transparent}}

/* QUEUE STATUS BAR */
.queue-status-bar{display:none;background:rgba(0,245,255,.05);border:1px solid rgba(0,245,255,.2);border-radius:8px;padding:10px 14px;margin-bottom:12px;font-family:'Noto Sans Thai',sans-serif;font-size:.78rem;color:var(--cy);align-items:center;gap:10px}
.queue-status-bar.show{display:flex}

/* SEARCH */
.search-results{margin-top:16px}
.result-item{display:flex;gap:12px;padding:12px;background:#030a12;border:1px solid #0d2030;border-radius:8px;margin-bottom:8px;cursor:pointer;transition:border-color .2s,background .2s;animation:fadeIn .3s both}
.result-item:hover{border-color:var(--cy);background:rgba(0,245,255,.04)}
.result-thumb{width:80px;height:45px;object-fit:cover;border-radius:4px;flex-shrink:0}
.result-info{flex:1;min-width:0}
.result-title{font-size:.82rem;font-weight:600;color:#ddd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}
.result-meta{font-family:'Share Tech Mono',monospace;font-size:.65rem;color:#4a7a9a}

/* LIST ITEMS */
.list-item{display:flex;align-items:center;gap:12px;padding:12px 14px;background:#030a12;border:1px solid #0d2030;border-radius:8px;margin-bottom:8px;animation:fadeIn .3s both}
.list-item:hover{border-color:#1a3a55}
.item-icon{font-size:1.1rem;flex-shrink:0}
.item-info{flex:1;min-width:0}
.item-title{font-size:.82rem;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.item-meta{font-family:'Share Tech Mono',monospace;font-size:.65rem;color:#3a6a8a}
.item-actions{display:flex;gap:6px;flex-shrink:0}
.btn-icon{width:28px;height:28px;border:none;border-radius:6px;background:#0a1a28;color:#4a7a9a;font-size:.8rem;cursor:pointer;transition:.2s;display:flex;align-items:center;justify-content:center}
.btn-icon:hover{background:#1a2a3a;color:var(--cy)}
.btn-icon.danger:hover{background:rgba(255,51,85,.15);color:var(--rd)}

/* STATS */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.stat-card{background:#030a12;border:1px solid #0d2030;border-radius:10px;padding:16px;text-align:center;transition:border-color .2s,transform .2s}
.stat-card:hover{border-color:var(--cy);transform:translateY(-2px)}
.stat-card-val{font-family:'Orbitron',sans-serif;font-size:1.3rem;font-weight:700;margin-bottom:4px}
.stat-card-val.cy{color:var(--cy);text-shadow:var(--glow-cy)}
.stat-card-val.gr{color:var(--gr);text-shadow:var(--glow-gr)}
.stat-card-val.rd{color:var(--rd)}
.stat-card-val.mg{color:var(--mg)}
.stat-card-label{font-family:'Noto Sans Thai',sans-serif;font-size:.65rem;color:#2a5a7a;letter-spacing:.05em}
.rate-bar-wrap{margin-top:12px}
.rate-bar{height:12px;background:#0a1a28;border-radius:6px;overflow:hidden;border:1px solid #1a3a55;margin:6px 0}
.rate-fill{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--gr),var(--cy));box-shadow:var(--glow-gr);transition:width 1s ease}

/* SETTINGS */
.settings-grid{display:grid;gap:12px}
.setting-row{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:#030a12;border:1px solid #0d2030;border-radius:8px;gap:12px;flex-wrap:wrap}
.setting-label{font-family:'Noto Sans Thai',sans-serif;font-size:.78rem;color:#7aaabb}
.setting-ctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}

/* TOAST */
.toast-wrap{position:fixed;bottom:20px;right:20px;z-index:999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{background:#0a1f30;border:1px solid var(--cy);border-radius:8px;padding:10px 16px;font-family:'Noto Sans Thai',sans-serif;font-size:.78rem;color:var(--cy);box-shadow:var(--glow-cy);animation:toastIn .3s ease,toastOut .3s 2.7s ease forwards;pointer-events:auto}
.toast.err{border-color:var(--rd);color:var(--rd);box-shadow:0 0 12px rgba(255,51,85,.3)}
.toast.suc{border-color:var(--gr);color:var(--gr);box-shadow:var(--glow-gr)}
@keyframes toastIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateX(20px)}}

/* LOADER */
.loader{display:inline-block;width:16px;height:16px;border:2px solid #1a3a55;border-top-color:var(--cy);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* EMPTY */
.empty{text-align:center;padding:32px;color:#2a5a7a;font-family:'Noto Sans Thai',sans-serif;font-size:.8rem}
.empty-icon{font-size:2rem;margin-bottom:8px;opacity:.4}

@media(max-width:520px){
  .logo{font-size:1.8rem}
  .nav-btn{font-size:.55rem;padding:8px 6px}
  .thumb{width:120px;height:68px}
}
</style>
</head>
<body>
<div class="scanlines"></div>
<div id="app">

  <!-- HEADER -->
  <header class="header">
    <div class="corner-tl"></div><div class="corner-tr"></div>
    <div class="corner-bl"></div><div class="corner-br"></div>
    <div class="logo">NEXLOAD</div>
    <div class="logo-sub">ดาวน์โหลด YOUTUBE — เวอร์ชัน 5.1</div>
    <div class="sys-time" id="sysTime"></div>
  </header>

  <!-- NAV -->
  <nav class="nav">
    <button class="nav-btn active" onclick="switchPanel('download',this)">
      <span class="nav-icon">⬇️</span>ดาวน์โหลด
    </button>
    <button class="nav-btn" onclick="switchPanel('multiurl',this)">
      <span class="nav-icon">📋</span>หลาย URL
    </button>
    <button class="nav-btn" onclick="switchPanel('search',this)">
      <span class="nav-icon">🔍</span>ค้นหา
    </button>
    <button class="nav-btn" onclick="switchPanel('queue',this)">
      <span class="nav-icon">📥</span>คิว
    </button>
  </nav>

  <!-- แผง: ดาวน์โหลด -->
  <div class="panel active" id="panel-download">
    <div class="card">
      <div class="section-title">▶ ดาวน์โหลดวิดีโอ</div>
      <div class="input-wrap">
        <input class="url-input" id="dlUrl" placeholder="วางลิงก์ YouTube ที่นี่..." type="url"
               onkeydown="if(event.key==='Enter')fetchInfo()">
        <button class="btn btn-cy" onclick="fetchInfo()" id="btnFetch">🔎 ตรวจสอบ</button>
      </div>
      <div class="typewriter" id="dlStatus" style="margin-top:8px">ระบบพร้อมทำงาน_</div>

      <div class="video-info" id="videoInfo">
        <div class="thumb-wrap">
          <img class="thumb" id="infoThumb" src="" alt="">
          <div class="thumb-overlay"></div>
        </div>
        <div class="video-meta">
          <div class="video-title" id="infoTitle"></div>
          <div class="video-tags">
            <span class="tag" id="infoCh"></span>
            <span class="tag mg" id="infoDur"></span>
            <span class="tag gr" id="infoViews"></span>
            <span class="tag" id="infoSz"></span>
          </div>
          <div class="dl-opts">
            <select class="select-box" id="dlRes">
              <option value="best">🎞 คุณภาพสูงสุด</option>
            </select>
            <div class="toggle-wrap">
              <div class="toggle" id="togAudio" onclick="toggleAudio()"></div>
              <span>เฉพาะเสียง (MP3)</span>
            </div>
            <button class="btn btn-mg" onclick="addQueueCurrent()">+ เพิ่มคิว</button>
            <button class="btn btn-gr" onclick="startDownload()" id="btnDl">▶ ดาวน์โหลด</button>
          </div>
        </div>
      </div>

      <div class="progress-wrap" id="progressWrap">
        <div class="progress-title" style="display:flex;justify-content:space-between;align-items:center">
          <span>⚡ กำลังดาวน์โหลด</span>
          <button class="btn btn-rd" id="btnCancel" onclick="cancelDownload()" style="padding:4px 12px;font-size:.65rem">✖ ยกเลิก</button>
        </div>
        <div class="progress-text" id="pTitle"></div>
        <div class="bar-wrap"><div class="bar-fill" id="pBar" style="width:0%"></div></div>
        <div class="progress-stats">
          <div class="stat-item"><div class="stat-label">ความคืบหน้า</div><div class="stat-val" id="pPct">0%</div></div>
          <div class="stat-item"><div class="stat-label">ความเร็ว</div><div class="stat-val" id="pSpeed">---</div></div>
          <div class="stat-item"><div class="stat-label">เวลาที่เหลือ</div><div class="stat-val" id="pEta">---</div></div>
          <div class="stat-item"><div class="stat-label">ขนาด</div><div class="stat-val" id="pSize">---</div></div>
        </div>
      </div>
      <div class="done-banner" id="doneBanner">
        <div class="done-icon">✅</div>
        <div class="done-details">
          <strong id="doneFile"></strong>
          <span id="doneMeta"></span>
        </div>
      </div>
      <div class="err-banner" id="errBanner"></div>
      <div class="err-banner" id="diskWarnBanner" style="border-color:rgba(255,204,0,.3);background:rgba(255,204,0,.07);color:#ffcc00"></div>
    </div>
  </div>

  <!-- แผง: หลาย URL -->
  <div class="panel" id="panel-multiurl">
    <div class="card">
      <div class="section-title">📋 ดาวน์โหลดหลาย URL พร้อมกัน</div>
      <p style="font-size:.78rem;color:#4a7a9a;margin-bottom:12px;font-family:'Noto Sans Thai',sans-serif">
        วางลิงก์ YouTube ทีละบรรทัด — สามารถใส่ได้หลายร้อยรายการ
      </p>
      <textarea class="url-input" id="multiUrls" rows="8"
        placeholder="https://www.youtube.com/watch?v=XXXXX&#10;https://www.youtube.com/watch?v=YYYYY&#10;https://www.youtube.com/watch?v=ZZZZZ"></textarea>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
        <button class="btn btn-cy" onclick="addMultiToQueue()">📥 เพิ่มทั้งหมดลงคิว</button>
        <button class="btn btn-gr" onclick="runMultiNow()">▶ ดาวน์โหลดทันที</button>
        <button class="btn btn-dim" onclick="document.getElementById('multiUrls').value=''">🗑 ล้าง URL</button>
      </div>
      <div id="multiStatus" style="margin-top:12px"></div>
    </div>
  </div>

  <!-- แผง: ค้นหา -->
  <div class="panel" id="panel-search">
    <div class="card">
      <div class="section-title">🔍 ค้นหา YouTube</div>
      <div class="input-wrap">
        <input class="url-input" id="searchKw" placeholder="พิมพ์คำค้นหา..."
               onkeydown="if(event.key==='Enter')doSearch()">
        <button class="btn btn-cy" onclick="doSearch()" id="btnSearch">ค้นหา</button>
      </div>
    </div>
    <div class="search-results" id="searchResults"></div>
  </div>

  <!-- แผง: คิว -->
  <div class="panel" id="panel-queue">
    <div class="card">
      <div class="section-title">📥 คิวดาวน์โหลด <span id="queueCount" style="font-family:'Share Tech Mono',monospace;font-size:.7rem;color:var(--mg);margin-left:8px"></span></div>
      <div class="queue-status-bar" id="queueStatusBar">
        <span class="loader"></span>
        <span id="queueStatusText">กำลังประมวลผลคิว...</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <button class="btn btn-gr" onclick="startAutoQueue()" id="btnRunQ">▶ เริ่มทั้งหมด</button>
        <button class="btn btn-rd" onclick="clearQueue()">🗑 ล้างคิว</button>
      </div>
      <div id="queueList"></div>
    </div>
  </div>

</div>
<div class="toast-wrap" id="toastWrap"></div>

<script>
// ═══════════════════════════════════════════
//  สถานะ
// ═══════════════════════════════════════════
let currentInfo = null;
let audioOnly = false;
let cfgCache = {};
let queuePollTimer = null;

// ═══════════════════════════════════════════
//  นาฬิกา
// ═══════════════════════════════════════════
function updateClock(){
  const now = new Date();
  document.getElementById('sysTime').textContent =
    `เวลา › ${now.toLocaleDateString('th-TH')} ${now.toLocaleTimeString('th-TH')}`;
}
setInterval(updateClock,1000); updateClock();

// ═══════════════════════════════════════════
//  สลับแผง
// ═══════════════════════════════════════════
function switchPanel(name, btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  if(btn) btn.classList.add('active');
  if(name==='queue') loadQueue();
}

// ═══════════════════════════════════════════
//  Toast
// ═══════════════════════════════════════════
function toast(msg, type=''){
  const tw = document.getElementById('toastWrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`; el.textContent = msg;
  tw.appendChild(el);
  setTimeout(()=>el.remove(), 3200);
}

// ═══════════════════════════════════════════
//  ดึงข้อมูลวิดีโอ
// ═══════════════════════════════════════════
async function fetchInfo(){
  const url = document.getElementById('dlUrl').value.trim();
  if(!url){ toast('กรุณาใส่ URL','err'); return; }
  setStatus('กำลังดึงข้อมูลวิดีโอ...');
  document.getElementById('btnFetch').disabled = true;
  document.getElementById('videoInfo').classList.remove('show');
  document.getElementById('doneBanner').classList.remove('show');
  document.getElementById('errBanner').classList.remove('show');
  try {
    const res = await fetch('/api/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const data = await res.json();
    if(data.error){ toast('❌ '+data.error,'err'); setStatus('เกิดข้อผิดพลาด'); return; }
    currentInfo = data;
    document.getElementById('infoThumb').src = data.thumbnail||'';
    document.getElementById('infoTitle').textContent = data.title;
    document.getElementById('infoCh').textContent = '📺 '+data.channel;
    document.getElementById('infoDur').textContent = '⏱ '+data.duration;
    document.getElementById('infoViews').textContent = '👁 '+data.views;
    document.getElementById('infoSz').textContent = '💾 '+data.size;
    const sel = document.getElementById('dlRes');
    sel.innerHTML = '<option value="best">🎞 คุณภาพสูงสุด</option>';
    (data.resolutions||[]).forEach(h=>{
      const o=document.createElement('option'); o.value=h; o.textContent=`📐 ${h}p`; sel.appendChild(o);
    });
    document.getElementById('videoInfo').classList.add('show');
    setStatus('พร้อมดาวน์โหลด ✔');
  } catch(e){ toast('เกิดข้อผิดพลาด','err'); setStatus('เกิดข้อผิดพลาด'); }
  finally{ document.getElementById('btnFetch').disabled = false; }
}

function setStatus(msg){ document.getElementById('dlStatus').textContent = msg+'_'; }
function toggleAudio(){ audioOnly=!audioOnly; document.getElementById('togAudio').classList.toggle('on',audioOnly); }

// ═══════════════════════════════════════════
//  เริ่มดาวน์โหลด
// ═══════════════════════════════════════════
async function startDownload(){
  if(!currentInfo){ toast('ตรวจสอบวิดีโอก่อน','err'); return; }
  const url = document.getElementById('dlUrl').value.trim();
  const res = document.getElementById('dlRes').value;
  document.getElementById('btnDl').disabled = true;
  document.getElementById('progressWrap').classList.add('show');
  document.getElementById('doneBanner').classList.remove('show');
  document.getElementById('errBanner').classList.remove('show');
  document.getElementById('pTitle').textContent = currentInfo.title;
  document.getElementById('pBar').style.width='0%';
  document.getElementById('pPct').textContent='0%';
  document.getElementById('pSpeed').textContent='---';
  document.getElementById('pEta').textContent='---';

  await fetch('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url,title:currentInfo.title,resolution:res,audio_only:audioOnly})});

  // ── ตรวจสอบดิสก์ก่อนโหลด ──
  try {
    const dk = await (await fetch('/api/diskspace')).json();
    const warn = document.getElementById('diskWarnBanner');
    if(dk.pct_used >= 90){
      warn.textContent = `⚠️ พื้นที่ดิสก์เหลือน้อย! ว่าง ${dk.free_str} จากทั้งหมด ${dk.total_str}`;
      warn.classList.add('show');
    } else { warn.classList.remove('show'); }
  } catch(e){}

  listenProgress();
}

function listenProgress(onDone){
  const es = new EventSource('/api/progress');
  es.onmessage = e=>{
    const d = JSON.parse(e.data);
    if(d.type==='progress'){
      document.getElementById('pBar').style.width=d.pct+'%';
      document.getElementById('pPct').textContent=d.pct+'%';
      document.getElementById('pSpeed').textContent=d.speed;
      document.getElementById('pEta').textContent=d.eta;
      document.getElementById('pSize').textContent=d.dl+' / '+d.total;
    } else if(d.type==='merging'){
      document.getElementById('pTitle').textContent='🔀 กำลังรวมไฟล์...';
    } else if(d.type==='done'){
      es.close();
      document.getElementById('progressWrap').classList.remove('show');
      document.getElementById('doneFile').textContent=d.file;
      document.getElementById('doneMeta').textContent=`ขนาด: ${d.size} | ใช้เวลา: ${d.elapsed}`;
      document.getElementById('doneBanner').classList.add('show');
      document.getElementById('btnDl').disabled=false;
      toast('ดาวน์โหลดสำเร็จ! 🎉','suc');
      setStatus('ดาวน์โหลดสำเร็จ ✔');
      // auto-download ไฟล์เข้าเครื่องผู้ใช้เลย
      const a = document.createElement('a');
      a.href = '/api/files/'+encodeURIComponent(d.file)+'/download';
      a.download = d.file;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      if(onDone) onDone(true);
    } else if(d.type==='cancelled'){
      es.close();
      document.getElementById('progressWrap').classList.remove('show');
      document.getElementById('btnDl').disabled=false;
      toast('ยกเลิกดาวน์โหลดแล้ว','');
      setStatus('ยกเลิก');
      if(onDone) onDone(false);
    } else if(d.type==='retry'){
      document.getElementById('pTitle').textContent=`⚠️ ครั้งที่ ${d.attempt} ล้มเหลว รอ ${d.wait} วินาที...`;
    } else if(d.type==='error'){
      es.close();
      document.getElementById('progressWrap').classList.remove('show');
      document.getElementById('errBanner').textContent='❌ '+d.msg;
      document.getElementById('errBanner').classList.add('show');
      document.getElementById('btnDl').disabled=false;
      toast('ดาวน์โหลดล้มเหลว','err');
      setStatus('เกิดข้อผิดพลาด');
      if(onDone) onDone(false);
    } else if(d.type==='queue_done'){
      es.close();
      document.getElementById('progressWrap').classList.remove('show');
      toast(`คิวเสร็จสิ้น ${d.total} รายการ 🎉`,'suc');
      setStatus('คิวดาวน์โหลดเสร็จสิ้น ✔');
      document.getElementById('btnDl').disabled=false;
      if(onDone) onDone(true);
    } else if(d.type==='ytdlp_updated'){
      const el = document.getElementById('updateStatus');
      if(el){ el.textContent = d.msg; el.style.color = d.ok ? 'var(--gr)' : 'var(--rd)'; }
      document.getElementById('btnUpdate').disabled=false;
      if(d.ok){ loadYtdlpVersion(); toast(d.msg,'suc'); } else { toast('อัปเดตล้มเหลว','err'); }
    }
  };
}

async function cancelDownload(){
  if(!confirm('ยกเลิกการดาวน์โหลดที่กำลังทำอยู่?')) return;
  await fetch('/api/cancel',{method:'POST'});
  toast('กำลังยกเลิก...','');
}

// ═══════════════════════════════════════════
//  คิว (Auto Worker)
// ═══════════════════════════════════════════
async function addQueueCurrent(){
  if(!currentInfo){ toast('ตรวจสอบวิดีโอก่อน','err'); return; }
  const url = document.getElementById('dlUrl').value.trim();
  const r = await fetch('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url,title:currentInfo.title})});
  const d = await r.json();
  toast(`เพิ่มคิวแล้ว (${d.count} รายการ)`,'suc');
}

async function loadQueue(){
  const res = await fetch('/api/queue');
  const q = await res.json();
  const el = document.getElementById('queueList');
  const countEl = document.getElementById('queueCount');
  if(countEl) countEl.textContent = q.length ? `[ ${q.length} รายการ ]` : '';
  if(!q.length){ el.innerHTML='<div class="empty"><div class="empty-icon">📭</div>ยังไม่มีรายการในคิว</div>'; return; }
  el.innerHTML = q.map((item,i)=>`
    <div class="list-item">
      <div class="item-icon">🎬</div>
      <div class="item-info">
        <div class="item-title">${esc(item.title||'ไม่ทราบชื่อ')}</div>
        <div class="item-meta">${esc(item.url)}</div>
      </div>
      <div class="item-actions">
        <button class="btn-icon danger" onclick="removeQueue(${i})" title="ลบ">🗑</button>
      </div>
    </div>`).join('');
}

async function removeQueue(i){
  await fetch(`/api/queue/${i}`,{method:'DELETE'});
  loadQueue(); toast('ลบออกจากคิวแล้ว');
}

async function clearQueue(){
  if(!confirm('ล้างคิวทั้งหมด?')) return;
  await fetch('/api/queue/clear',{method:'DELETE'});
  loadQueue(); toast('ล้างคิวแล้ว');
}

async function startAutoQueue(){
  const r = await fetch('/api/queue/run',{method:'POST'});
  const d = await r.json();
  if(d.error){ toast(d.error,'err'); return; }
  toast(`เริ่มคิว ${d.total} รายการ...`,'suc');
  // switch to download panel to show progress
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('panel-download').classList.add('active');
  document.querySelectorAll('.nav-btn')[0].classList.add('active');
  document.getElementById('progressWrap').classList.add('show');
  document.getElementById('doneBanner').classList.remove('show');
  document.getElementById('errBanner').classList.remove('show');
  document.getElementById('pBar').style.width='0%';
  document.getElementById('pPct').textContent='0%';
  document.getElementById('pTitle').textContent='กำลังเริ่มต้น...';
  document.getElementById('btnDl').disabled=true;
  listenProgress(()=>{ loadQueue(); });
  startQueueStatusPoll();
}

function startQueueStatusPoll(){
  if(queuePollTimer) clearInterval(queuePollTimer);
  queuePollTimer = setInterval(async()=>{
    const s = await (await fetch('/api/queue/status')).json();
    const bar = document.getElementById('queueStatusBar');
    const txt = document.getElementById('queueStatusText');
    if(s.running){
      bar.classList.add('show');
      txt.textContent = `กำลังดาวน์โหลด: ${s.current} (${s.done+1}/${s.total})`;
      if(s.current) document.getElementById('pTitle').textContent = s.current;
    } else {
      bar.classList.remove('show');
      clearInterval(queuePollTimer);
    }
  }, 1500);
}

// ═══════════════════════════════════════════
//  หลาย URL
// ═══════════════════════════════════════════
function parseMultiUrls(){
  const raw = document.getElementById('multiUrls').value;
  return raw.split('\n').map(l=>l.trim()).filter(l=>l.startsWith('http'));
}

async function fetchTitlesForUrls(urls){
  const ms = document.getElementById('multiStatus');
  ms.innerHTML = `<div style="font-size:.78rem;color:var(--cy);font-family:'Noto Sans Thai',sans-serif;padding:8px 0">⏳ กำลังดึงชื่อวิดีโอ 0/${urls.length}...</div>`;
  const items = [];
  for(let i=0; i<urls.length; i++){
    ms.innerHTML = `<div style="font-size:.78rem;color:var(--cy);font-family:'Noto Sans Thai',sans-serif;padding:8px 0">⏳ กำลังดึงชื่อวิดีโอ ${i+1}/${urls.length}...</div>`;
    try {
      const res = await fetch('/api/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:urls[i]})});
      const d = await res.json();
      items.push({url:urls[i], title: d.title || urls[i]});
    } catch(e){
      items.push({url:urls[i], title: urls[i]});
    }
  }
  return items;
}

async function addMultiToQueue(){
  const urls = parseMultiUrls();
  if(!urls.length){ toast('ไม่พบ URL ที่ถูกต้อง','err'); return; }
  const items = await fetchTitlesForUrls(urls);
  const r = await fetch('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({items})});
  const d = await r.json();
  toast(`เพิ่ม ${urls.length} รายการลงคิวแล้ว`,'suc');
  const ms = document.getElementById('multiStatus');
  ms.innerHTML = `<div style="font-size:.78rem;color:var(--gr);font-family:'Noto Sans Thai',sans-serif;padding:8px 0">✅ เพิ่ม ${urls.length} URL ลงคิวแล้ว — ไปที่แผง "คิว" แล้วกด "เริ่มทั้งหมด"</div>`;
}

async function runMultiNow(){
  const urls = parseMultiUrls();
  if(!urls.length){ toast('ไม่พบ URL ที่ถูกต้อง','err'); return; }
  const items = await fetchTitlesForUrls(urls);
  await fetch('/api/queue/clear',{method:'DELETE'});
  await fetch('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
  toast(`เพิ่ม ${urls.length} รายการและเริ่มดาวน์โหลด...`,'suc');
  const ms = document.getElementById('multiStatus');
  ms.innerHTML = `<div style="font-size:.78rem;color:var(--cy);font-family:'Noto Sans Thai',sans-serif;padding:8px 0">⚡ กำลังเริ่มต้น...</div>`;
  await startAutoQueue();
}

// ═══════════════════════════════════════════
//  ค้นหา
// ═══════════════════════════════════════════
async function doSearch(){
  const kw = document.getElementById('searchKw').value.trim();
  if(!kw){ toast('กรุณาใส่คำค้นหา','err'); return; }
  document.getElementById('btnSearch').disabled=true;
  document.getElementById('searchResults').innerHTML='<div class="empty"><div class="loader"></div></div>';
  try {
    const res = await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:kw})});
    const d = await res.json();
    if(d.error){ toast(d.error,'err'); return; }
    const el = document.getElementById('searchResults');
    if(!d.results.length){ el.innerHTML='<div class="empty"><div class="empty-icon">🔍</div>ไม่พบผลลัพธ์</div>'; return; }
    el.innerHTML = d.results.map(item=>`
      <div class="result-item" onclick="selectResult('${esc(item.url)}','${esc(item.title.replace(/'/g,"\\'"))}')">
        <img class="result-thumb" src="${esc(item.thumbnail||'')}" alt="" onerror="this.style.display='none'">
        <div class="result-info">
          <div class="result-title">${esc(item.title)}</div>
          <div class="result-meta">⏱ ${item.duration} | 👁 ${item.views} | 📺 ${esc(item.channel)}</div>
        </div>
        <button class="btn btn-dim" style="padding:6px 10px;font-size:.6rem">เลือก</button>
      </div>`).join('');
  } catch(e){ toast('เกิดข้อผิดพลาด','err'); }
  finally{ document.getElementById('btnSearch').disabled=false; }
}

function selectResult(url, title){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach((b,i)=>{if(i===0) b.classList.add('active'); else b.classList.remove('active');});
  document.getElementById('panel-download').classList.add('active');
  document.getElementById('dlUrl').value=url;
  fetchInfo();
  toast('เลือกวิดีโอแล้ว กำลังโหลดข้อมูล...');
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function fmtSz(b){
  if(!b) return '0 B';
  const u=['B','KB','MB','GB'];
  for(const s of u){ if(b<1024) return b.toFixed(1)+' '+s; b/=1024; }
  return b.toFixed(1)+' TB';
}
</script>
</body>
</html>"""

@app.route("/")
def index():
    return HTML

@app.route("/api/log")
def api_log():
    if os.path.exists("nexload.log"):
        return send_file("nexload.log", mimetype="text/plain; charset=utf-8")
    return "ยังไม่มี log", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("\n" + "▓"*52)
    print("  ███  NEXLOAD WEB  v5.1  ███")
    print(f"  http://0.0.0.0:{port}")
    print("▓"*52 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
