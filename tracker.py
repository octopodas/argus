#!/usr/bin/env python3
"""Activity tracker: input + camera signals -> sqlite -> local dashboard.

Privacy: stores only counts and derived metrics. No key contents, no frames.
Run: .venv/bin/python tracker.py          dashboard at http://localhost:8787
     .venv/bin/python tracker.py --selftest
"""
import json, math, os, signal, sqlite3, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "activity.db")
CONFIG_PATH = os.path.join(BASE, "config.json")

DEFAULTS = {
    "break_every_min": 50,      # remind after this many active minutes
    "snooze_min": 5,            # re-remind interval while break not taken
    "idle_reset_min": 3,        # this many idle minutes = break taken
    "camera_enabled": True,
    "camera_fps": 8,
    "port": 8787,
    "notify": True,
    "ollama_model": "",         # empty = first available model
}

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

CFG = load_config()
LOCK = threading.Lock()
STOP = threading.Event()

# per-minute accumulators, flushed by the aggregator
C = dict(keys=0, backspaces=0, clicks=0, scrolls=0, mouse_px=0.0,
         frames=0, present=0, blinks=0, brow=0.0, press=0.0, away=0, close=0,
         switches=0, chews=0)
STREAK = 0                # live active-minute streak, mirrored by aggregator
APP_SECONDS = {}          # app -> seconds, current minute
LAST_INPUT = time.time()

def db():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS minutes(
            ts INTEGER PRIMARY KEY, keys INT, backspaces INT, clicks INT,
            scrolls INT, mouse_px REAL, frames INT, present INT, blinks INT,
            brow REAL, press REAL, away INT, close INT, switches INT, active INT);
        CREATE TABLE IF NOT EXISTS apps(date TEXT, app TEXT, seconds INT,
            PRIMARY KEY(date, app));
        CREATE TABLE IF NOT EXISTS events(ts INTEGER, kind TEXT, detail TEXT);
        """)
        try:
            conn.execute("ALTER TABLE minutes ADD COLUMN chews INT DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists

# ---------- input listeners (pynput) ----------

def start_input_listeners():
    from pynput import keyboard, mouse
    last_pos = [None]

    def on_press(key):
        global LAST_INPUT
        with LOCK:
            C["keys"] += 1
            if key == keyboard.Key.backspace or key == keyboard.Key.delete:
                C["backspaces"] += 1
        LAST_INPUT = time.time()

    def on_click(x, y, button, pressed):
        global LAST_INPUT
        if pressed:
            with LOCK:
                C["clicks"] += 1
            LAST_INPUT = time.time()

    def on_scroll(x, y, dx, dy):
        global LAST_INPUT
        with LOCK:
            C["scrolls"] += 1
        LAST_INPUT = time.time()

    def on_move(x, y):
        global LAST_INPUT
        if last_pos[0]:
            d = math.hypot(x - last_pos[0][0], y - last_pos[0][1])
            if d < 3000:  # ignore multi-monitor jumps
                with LOCK:
                    C["mouse_px"] += d
        last_pos[0] = (x, y)
        LAST_INPUT = time.time()

    keyboard.Listener(on_press=on_press, daemon=True).start()
    mouse.Listener(on_click=on_click, on_scroll=on_scroll, on_move=on_move,
                   daemon=True).start()

# ---------- camera worker (mediapipe face landmarker) ----------

def camera_worker():
    import cv2
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    import mediapipe as mp

    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(
            model_asset_path=os.path.join(BASE, "face_landmarker.task")),
        running_mode=vision.RunningMode.VIDEO,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        num_faces=1)
    landmarker = vision.FaceLandmarker.create_from_options(opts)
    # camera is opened lazily and RELEASED while camera_enabled is off, so
    # the toggle frees /dev/video0 for video calls (V4L2 streaming is
    # exclusive — holding it would black out the call's camera)
    cap = None

    eye_closed = False
    jaw_open = False
    face_h_ema = None
    t0 = time.time()
    # eye-rub detection: hands occluding the face make detection flicker.
    # Calibrated 2026-07-16: a real rub produced ~20 presence toggles in 17s;
    # normal viewing produces none, walking away produces one clean gap.
    # User rubs deliberately for 10-20s, so the flicker must also SPAN >=8s —
    # this rejects brief occlusions (hand pass, mug) that could toggle 6 times.
    # 43 false positives on 2026-07-16 from marginal detection with the head
    # turned to the side monitor: face present 80-100% with brief dropouts.
    # A real rub hides the face, so also require >=40% face-absent frames
    # in the 20s window.
    was_present = None
    toggles = []
    pres_win = []
    last_rub = 0.0
    while not STOP.is_set():
        if not CFG["camera_enabled"]:
            if cap is not None:
                cap.release(); cap = None
            time.sleep(2); continue
        if cap is None:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                # busy (a call holds it?) — retry instead of dying
                cap.release(); cap = None
                time.sleep(10); continue
        ok, frame = cap.read()
        if not ok:
            time.sleep(2); continue
        ts_ms = int((time.time() - t0) * 1000)
        img = mp.Image(image_format=mp.ImageFormat.SRGB,
                       data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        res = landmarker.detect_for_video(img, ts_ms)
        now = time.time()
        present_now = bool(res.face_landmarks)
        if was_present is not None and present_now != was_present:
            toggles.append(now)
        was_present = present_now
        toggles = [t for t in toggles if now - t < 20]
        pres_win.append((now, present_now))
        pres_win = [(t, p) for t, p in pres_win if now - t < 20]
        absent = sum(1 for _, p in pres_win if not p) / len(pres_win)
        if (len(toggles) >= 6 and toggles[-1] - toggles[0] >= 8
                and absent >= 0.4 and now - last_rub > 60):
            last_rub = now
            log_event("eye_rub")
        with LOCK:
            C["frames"] += 1
            if res.face_landmarks:
                C["present"] += 1
                bs = {b.category_name: b.score for b in res.face_blendshapes[0]}
                blink = (bs["eyeBlinkLeft"] + bs["eyeBlinkRight"]) / 2
                # thresholds tuned low: blendshape amplitude drops ~40% when
                # the head is turned toward a side monitor (measured 2026-07-16)
                if blink > 0.35 and not eye_closed:
                    eye_closed = True
                elif blink < 0.22 and eye_closed:
                    eye_closed = False
                    C["blinks"] += 1
                # chewing: rhythmic small jawOpen oscillation. Calibrated
                # 2026-07-16: facing camera, gum chewing cycles ~70/min
                # (median jawOpen 0.05, p90 0.13); head turned to the side
                # monitor dampens amplitude ~40% (same as blinks), dropping
                # counts to 12-23/min at 0.08/0.05 — hence the low thresholds
                # and the >=15 cycles/min bar for a chewing minute.
                # ponytail: talking also cycles the jaw — accepted false
                # positive; gate on mouthClose if it shows up in practice.
                if bs["jawOpen"] > 0.065 and not jaw_open:
                    jaw_open = True
                elif bs["jawOpen"] < 0.045 and jaw_open:
                    jaw_open = False
                    C["chews"] += 1
                C["brow"] += (bs["browDownLeft"] + bs["browDownRight"]) / 2
                C["press"] += (bs["mouthPressLeft"] + bs["mouthPressRight"]) / 2
                m = res.facial_transformation_matrixes[0]
                yaw = math.degrees(math.atan2(m[0][2], m[2][2]))
                pitch = math.degrees(math.asin(max(-1, min(1, -m[1][2]))))
                if abs(yaw) > 30 or pitch < -25:
                    C["away"] += 1
                ys = [p.y for p in res.face_landmarks[0]]
                face_h = max(ys) - min(ys)
                # ponytail: EMA baseline for "leaning too close"; a
                # calibration step would be more exact
                face_h_ema = face_h if face_h_ema is None else 0.995 * face_h_ema + 0.005 * face_h
                if face_h > face_h_ema * 1.25:
                    C["close"] += 1
        STOP.wait(1.0 / max(1, CFG["camera_fps"]))
    if cap is not None:
        cap.release()

# ---------- active window sampler (xdotool) ----------

def window_worker():
    last = None
    while not STOP.is_set():
        try:
            wid = subprocess.run(["xdotool", "getactivewindow"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            out = subprocess.run(["xprop", "-id", wid, "WM_CLASS"],
                                 capture_output=True, text=True, timeout=3).stdout
            app = out.split('"')[-2] if '"' in out else ""
        except Exception:
            app = ""
        if app:
            with LOCK:
                APP_SECONDS[app] = APP_SECONDS.get(app, 0) + 10
                if last and app != last:
                    C["switches"] += 1
            last = app
        STOP.wait(10)

# ---------- break reminders + minute aggregator ----------

def notify(title, body):
    if CFG["notify"]:
        subprocess.run(["notify-send", "-u", "critical", "-a", "ActivityTracker",
                        title, body], check=False)

def log_event(kind, detail=""):
    with db() as conn:
        conn.execute("INSERT INTO events VALUES(?,?,?)",
                     (int(time.time()), kind, detail))

def seed_streak():
    """Resume the active-minute streak from the DB so a service restart
    doesn't silently postpone break reminders."""
    cutoff = int(time.time()) - 6 * 3600
    rows = dict(db().execute(
        "SELECT ts, active FROM minutes WHERE ts > ?", (cutoff,)).fetchall())
    streak, idle = 0, 0
    t = int(time.time()) // 60 * 60 - 60
    while t > cutoff and idle < CFG["idle_reset_min"]:
        if rows.get(t):
            streak += 1
            idle = 0
        else:
            idle += 1
        t -= 60
    return streak

def aggregator():
    global STREAK
    active_streak = STREAK = seed_streak()
    idle_run = 0
    last_reminder = 0.0
    while not STOP.is_set():
        STOP.wait(60 - time.time() % 60)
        if STOP.is_set():
            break
        now = int(time.time())
        with LOCK:
            row = dict(C)
            apps = dict(APP_SECONDS)
            for k in C:
                C[k] = 0 if not isinstance(C[k], float) else 0.0
            APP_SECONDS.clear()
        had_input = row["keys"] + row["clicks"] + row["scrolls"] > 0 or row["mouse_px"] > 50
        present = row["frames"] > 0 and row["present"] / row["frames"] > 0.5
        active = 1 if (had_input or present) else 0
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (now - 60, row["keys"], row["backspaces"], row["clicks"],
                          row["scrolls"], round(row["mouse_px"]), row["frames"],
                          row["present"], row["blinks"], round(row["brow"], 3),
                          round(row["press"], 3), row["away"], row["close"],
                          row["switches"], active, row["chews"]))
            date = time.strftime("%Y-%m-%d")
            for app, sec in apps.items():
                conn.execute("""INSERT INTO apps VALUES(?,?,?) ON CONFLICT(date,app)
                                DO UPDATE SET seconds=seconds+excluded.seconds""",
                             (date, app, sec))
        if active:
            active_streak += 1
            idle_run = 0
        else:
            idle_run += 1
            if idle_run >= CFG["idle_reset_min"] and active_streak > 0:
                if active_streak >= CFG["break_every_min"]:
                    log_event("break_taken", str(active_streak))
                active_streak = 0
        STREAK = active_streak
        if active_streak >= CFG["break_every_min"] and \
           time.time() - last_reminder >= CFG["snooze_min"] * 60:
            notify("Time for a break 🧘",
                   f"You've been active for {active_streak} min. "
                   f"Step away for a few minutes — look at something 6m away.")
            log_event("break_reminder", str(active_streak))
            last_reminder = time.time()

# ---------- scoring ----------

def stress_score(mins):
    """0-100. Weighted mix of face tension, blink-rate deviation,
    correction rate and typing burstiness. Transparent heuristic, not medicine."""
    act = [m for m in mins if m["active"]]
    if not act:
        return 0, {}
    pres = [m for m in act if m["present"]]
    brow = sum(m["brow"] / max(1, m["present"]) for m in pres) / len(pres) if pres else 0
    press = sum(m["press"] / max(1, m["present"]) for m in pres) / len(pres) if pres else 0
    tension = min(1.0, (brow + press) * 2.5)
    bpm = sum(m["blinks"] for m in pres) / len(pres) if pres else 0
    # normal ~15-20/min; both very low (strain) and very high (stress) deviate
    blink_dev = min(1.0, abs(bpm - 17) / 17) if pres else 0
    keys = sum(m["keys"] for m in act)
    corr = min(1.0, (sum(m["backspaces"] for m in act) / keys) / 0.25) if keys else 0
    krates = [m["keys"] for m in act if m["keys"] > 0]
    burst = 0.0
    if len(krates) > 5:
        mean = sum(krates) / len(krates)
        sd = math.sqrt(sum((k - mean) ** 2 for k in krates) / len(krates))
        burst = min(1.0, (sd / mean) / 2)
    score = round(100 * (0.35 * tension + 0.25 * blink_dev + 0.2 * corr + 0.2 * burst))
    return score, dict(tension=round(tension, 2), blink_dev=round(blink_dev, 2),
                       correction=round(corr, 2), burstiness=round(burst, 2),
                       blinks_per_min=round(bpm, 1))

def focus_score(mins):
    """% of active minutes that look focused: <=2 app switches, some input,
    not mostly looking away."""
    act = [m for m in mins if m["active"]]
    if not act:
        return 0
    good = [m for m in act if m["switches"] <= 2
            and (m["keys"] + m["clicks"] > 0 or m["present"])
            and (m["frames"] == 0 or m["away"] / max(1, m["frames"]) < 0.4)]
    return round(100 * len(good) / len(act))

def day_summary(date):
    start = int(time.mktime(time.strptime(date, "%Y-%m-%d")))
    end = start + 86400
    cols = ("ts keys backspaces clicks scrolls mouse_px frames present blinks "
            "brow press away close switches active chews").split()
    with db() as conn:
        mins = [dict(zip(cols, r)) for r in conn.execute(
            "SELECT * FROM minutes WHERE ts>=? AND ts<? ORDER BY ts", (start, end))]
        apps = conn.execute("SELECT app, seconds FROM apps WHERE date=? "
                            "ORDER BY seconds DESC LIMIT 10", (date,)).fetchall()
        events = conn.execute("SELECT ts, kind, detail FROM events WHERE ts>=? AND ts<?",
                              (start, end)).fetchall()
    act = [m for m in mins if m["active"]]
    streak = best = 0
    prev = None
    for m in mins:
        if m["active"]:
            streak = streak + 1 if prev and m["ts"] - prev <= 120 else 1
            best = max(best, streak)
            prev = m["ts"]
    stress, parts = stress_score(mins)
    pres_min = sum(1 for m in mins if m["frames"] and m["present"] / m["frames"] > 0.5)
    # away time = inactive minutes inside the first-to-last-activity span,
    # so night hours before/after the workday don't count
    span_min = (act[-1]["ts"] - act[0]["ts"]) // 60 + 1 if act else 0
    away_min = span_min - len(act)
    return {
        "date": date,
        "totals": {
            "active_min": len(act), "present_min": pres_min,
            "away_min": away_min, "span_min": span_min,
            "keys": sum(m["keys"] for m in act),
            "clicks": sum(m["clicks"] for m in act),
            "mouse_m": round(sum(m["mouse_px"] for m in act) / 3780, 1),  # ~px->m at 96dpi
            "longest_streak_min": best,
            "reminders": sum(1 for e in events if e[1] == "break_reminder"),
            "breaks_taken": sum(1 for e in events if e[1] == "break_taken"),
            "eye_rubs": sum(1 for e in events if e[1] == "eye_rub"),
            "chew_min": sum(1 for m in mins if m["chews"] >= 15),
        },
        "stress": {"score": stress, "components": parts},
        "focus": {"score": focus_score(mins)},
        "minutes": [{"ts": m["ts"], "keys": m["keys"], "clicks": m["clicks"],
                     "active": m["active"],
                     "present": round(m["present"] / m["frames"], 2) if m["frames"] else None,
                     "away": round(m["away"] / m["frames"], 2) if m["frames"] else 0,
                     "tension": round((m["brow"] + m["press"]) / max(1, m["present"]), 3),
                     "chew": m["chews"] >= 15}
                    for m in mins],
        "apps": [list(a) for a in apps],
    }

def history(days=14):
    out = []
    for i in range(days - 1, -1, -1):
        date = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        s = day_summary(date)
        out.append({"date": date, "active_min": s["totals"]["active_min"],
                    "focus": s["focus"]["score"], "stress": s["stress"]["score"],
                    "keys": s["totals"]["keys"],
                    "breaks": s["totals"]["breaks_taken"],
                    "longest_streak_min": s["totals"]["longest_streak_min"],
                    "top_app": s["apps"][0][0] if s["apps"] else None})
    return out

# ---------- LLM insights (local ollama) ----------

def ollama_insights(summary):
    try:
        model = CFG["ollama_model"]
        if not model:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
                models = json.load(r).get("models", [])
            if not models:
                raise RuntimeError("no models")
            model = models[0]["name"]
        prompt = (
            "You are a productivity and wellbeing coach. Based on this JSON of "
            "someone's computer-activity day (times are minutes; stress/focus are "
            "0-100 heuristics; tension is facial muscle tension from camera), give "
            "3-5 short, concrete, non-generic observations and suggestions. "
            "Format as markdown: a few sections with short bold titles and "
            "bullet points under each. No preamble.\n\n" + json.dumps({k: summary[k] for k in
                ("totals", "stress", "focus", "apps")}))
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return {"model": model, "text": json.load(r)["response"].strip()}
    except Exception as e:
        return {"model": "heuristic", "text": heuristic_insights(summary),
                "note": f"ollama unavailable ({e})"}

def heuristic_insights(s):
    out = []
    t, st = s["totals"], s["stress"]
    if t["longest_streak_min"] >= CFG["break_every_min"] + 15:
        out.append(f"- Longest unbroken stretch was {t['longest_streak_min']} min — well past your {CFG['break_every_min']} min break target.")
    bpm = st["components"].get("blinks_per_min", 0)
    if 0 < bpm < 10:
        out.append(f"- Blink rate {bpm}/min is low (normal 15-20): classic screen-strain sign. Try the 20-20-20 rule.")
    if st["components"].get("correction", 0) > 0.6:
        out.append("- High typo/correction rate — often a fatigue or rushing signal.")
    if st["score"] >= 60:
        drivers = {k: v for k, v in st["components"].items() if k != "blinks_per_min"}
        out.append(f"- Stress index {st['score']}/100 is elevated today; the biggest driver is {max(drivers, key=drivers.get)}.")
    if s["focus"]["score"] < 50 and t["active_min"] > 60:
        out.append(f"- Focus {s['focus']['score']}%: lots of app switching. Consider batching communication apps.")
    if t.get("eye_rubs", 0) >= 2:
        out.append(f"- You rubbed your eyes {t['eye_rubs']} times today — an eye-strain signal. Check screen brightness/distance and try the 20-20-20 rule.")
    if t["reminders"] > t["breaks_taken"]:
        out.append(f"- You got {t['reminders']} break reminders but took {t['breaks_taken']} breaks.")
    return "\n".join(out) or "- Not enough data yet today. Keep the tracker running."

# ---------- http server ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        date = time.strftime("%Y-%m-%d")
        for part in query.split("&"):
            if part.startswith("date="):
                date = part[5:]
        if path == "/":
            with open(os.path.join(BASE, "dashboard.html"), "rb") as f:
                self._send(f.read(), "text/html")
        elif path == "/api/summary":
            s = day_summary(date)
            s["analysis"] = heuristic_insights(s)
            if date == time.strftime("%Y-%m-%d"):
                s["streak_min"] = STREAK
            self._send(s)
        elif path == "/api/history":
            self._send(history())
        elif path == "/api/insights":
            self._send(ollama_insights(day_summary(date)))
        elif path == "/api/config":
            self._send(CFG)
        else:
            self._send({"error": "not found"}, code=404)

    def do_POST(self):
        if self.path == "/api/config":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                incoming = json.loads(body)
                # merge over the file's current values, not this process's
                # snapshot — a stale in-memory CFG must not clobber the file
                CFG.update(load_config())
                for k in DEFAULTS:
                    if k in incoming and type(incoming[k]) == type(DEFAULTS[k]):
                        CFG[k] = incoming[k]
                save_config(CFG)
                self._send(CFG)
            except (json.JSONDecodeError, TypeError):
                self._send({"error": "bad json"}, code=400)
        else:
            self._send({"error": "not found"}, code=404)

# ---------- selftest ----------

def selftest():
    mins = [dict(ts=i * 60, keys=40, backspaces=4, clicks=5, scrolls=2,
                 mouse_px=500, frames=60, present=55, blinks=8, brow=5.5,
                 press=2.2, away=5, close=0, switches=1, active=1)
            for i in range(30)]
    score, parts = stress_score(mins)
    assert 0 <= score <= 100, score
    assert parts["blinks_per_min"] == 8.0
    assert focus_score(mins) == 100
    mins2 = [dict(m, switches=6) for m in mins]
    assert focus_score(mins2) == 0
    assert stress_score([])[0] == 0
    global DB
    DB = "/tmp/at_selftest.db"
    try:
        init_db()
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (0, 1, 0, 1, 0, 10.0, 6, 6, 1, 0.1, 0.1, 0, 0, 0, 1, 0))
        assert db().execute("SELECT COUNT(*) FROM minutes").fetchone()[0] == 1
    finally:
        os.unlink(DB)
    print("selftest ok")

def main():
    if "--selftest" in sys.argv:
        selftest(); return
    init_db()
    log_event("session_start")
    start_input_listeners()
    threading.Thread(target=camera_worker, daemon=True).start()
    threading.Thread(target=window_worker, daemon=True).start()
    threading.Thread(target=aggregator, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", CFG["port"]), Handler)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print(f"activity tracker running — dashboard: http://localhost:{CFG['port']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    STOP.set()

if __name__ == "__main__":
    main()
