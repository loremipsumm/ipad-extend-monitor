"""
iPad-as-extended-monitor server.

Captures the virtual monitor (or the laptop screen, in duplicate mode) with DXGI
Desktop Duplication and streams it to the iPad over a WebSocket, as hardware-encoded
H.264 (HTTPS page, decoded by Safari's WebCodecs) or as JPEG frames (plain HTTP
fallback). Touch, trackpad, Apple Pencil and keyboard input from the iPad is injected
as real Windows input; audio and the clipboard are shared too.

Run on the Windows laptop (or double-click launcher.pyw for a tray icon):
    pip install -r requirements.txt
    python server.py

Then open https://<this-pc-ip>:<https_port>/ in Safari on the iPad.
"""
import os
import subprocess
import sys

RESTART_EXIT_CODE = 3
CHILD_ENV = "IPAD_EXTEND_CHILD"


def supervise():
    """Run the real server in a child process and start it again whenever it exits to
    pick up a changed display setup (dxcam only enumerates displays once per process)."""
    env = {**os.environ, CHILD_ENV: "1"}
    while True:
        try:
            code = subprocess.call([sys.executable, os.path.abspath(__file__)], env=env)
        except KeyboardInterrupt:
            return 0
        if code != RESTART_EXIT_CODE:
            return code
        print("\nDisplay setup changed - restarting the server...\n", flush=True)


# Checked before the heavy imports below, so the supervisor stays a tiny process.
if __name__ == "__main__" and os.environ.get(CHILD_ENV) != "1":
    sys.exit(supervise())

import ctypes  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import ssl  # noqa: E402
import base64  # noqa: E402
import io  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402
from fractions import Fraction  # noqa: E402
from pathlib import Path  # noqa: E402

# Physical pixels everywhere (capture, cursor, input), not DPI-virtualized coordinates.
ctypes.windll.shcore.SetProcessDpiAwareness(2)

import av  # noqa: E402
import dxcam  # noqa: E402
from av.video.reformatter import VideoReformatter  # noqa: E402
import numpy as np  # noqa: E402
import simplejpeg  # noqa: E402
from flask import Flask, jsonify, render_template, send_file  # noqa: E402
from flask_sock import Sock  # noqa: E402
from PIL import Image  # noqa: E402
from werkzeug.serving import ThreadedWSGIServer, make_server  # noqa: E402

import winput  # noqa: E402
from audio import AudioBroadcaster  # noqa: E402
from camera import CameraBridge  # noqa: E402
from certs import ensure_certs  # noqa: E402
from wdisplay import cursor_on, display_signature, list_displays, pick_displays, render_cursor, set_portrait  # noqa: E402

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
CERT_DIR = HERE / "certs"


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate Notepad's BOM
    except (OSError, ValueError):
        return default


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------- cursor shapes
# The iPad draws the cursor itself, so it follows a finger or the trackpad instantly however
# slow the WiFi is. The video never contains the cursor; the server only sends where it is
# and, once per shape, what it looks like.

cursor_shapes = {}  # cursor handle -> shape sent to the iPad, or None if Windows won't render it
cursor_shapes_lock = threading.Lock()


def _png_data_url(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _encode_shape(handle, img):
    h, w = img.invert.shape
    alpha = img.alpha  # (h, w, 1), 0..1
    rgb = np.where(alpha > 0, img.color / np.maximum(alpha, 1e-6), 0)[..., ::-1]  # un-premultiply, BGR->RGB
    rgba = np.dstack([rgb, alpha * 255]).clip(0, 255).astype(np.uint8)
    shape = {"id": str(handle), "w": w, "h": h, "hot": [img.hot_x, img.hot_y], "png": _png_data_url(rgba)}
    if img.invert.any():
        # Pixels that invert the screen (the text I-beam): the iPad draws these white with
        # mix-blend-mode: difference, which inverts whatever is underneath.
        mask = np.zeros((h, w, 4), np.uint8)
        mask[img.invert] = 255
        shape["invert"] = _png_data_url(mask)
    return shape


def cursor_shape(handle):
    with cursor_shapes_lock:
        if handle not in cursor_shapes:
            img = render_cursor(handle)
            cursor_shapes[handle] = None if img is None else _encode_shape(handle, img)
        return cursor_shapes[handle]


# ---------------------------------------------------------------- encoding


def encode_jpeg(frame, quality):
    return simplejpeg.encode_jpeg(frame, quality=quality, colorspace="BGRA", colorsubsampling="420", fastdct=True)


# Tried in order: Intel QuickSync first, CPU x264 if no usable GPU encoder.
H264_ENCODERS = [
    ("h264_qsv", "nv12", {"async_depth": "1", "preset": "veryfast"}),
    ("libx264", "yuv420p", {"preset": "ultrafast", "tune": "zerolatency"}),
]


class H264Encoder:
    def __init__(self, width, height, quality):
        errors = []
        for name, pix_fmt, options in H264_ENCODERS:
            ctx = av.CodecContext.create(name, "w")
            ctx.width, ctx.height, ctx.pix_fmt = width, height, pix_fmt
            ctx.time_base, ctx.framerate = Fraction(1, 60), Fraction(60, 1)
            ctx.gop_size, ctx.max_b_frames = 600, 0
            # Constant quality (QSV ICQ / x264 CRF) keeps text sharp on the last
            # frame of a change, since nothing gets sent afterwards to refine it.
            ctx.options = {**options, "global_quality" if name == "h264_qsv" else "crf": str(quality)}
            try:
                ctx.open()
            except Exception as e:
                errors.append(f"{name}: {e}")
                continue
            self.ctx, self.name, self.pix_fmt = ctx, name, pix_fmt
            self.width, self.height = width, height
            self.pts = 0
            # One BGRA frame and colour converter reused for every frame: building them
            # per frame (VideoFrame.from_ndarray + reformat) cost ~16 ms at 2360x1640,
            # reusing them ~3 ms.
            self.bgra = av.VideoFrame(width, height, "bgra")
            plane = self.bgra.planes[0]
            rows = np.frombuffer(plane, dtype=np.uint8).reshape(height, plane.line_size)
            self.pixels = rows[:, :width * 4].reshape(height, width, 4)
            self.reformatter = VideoReformatter()
            return
        raise RuntimeError("No usable H.264 encoder: " + "; ".join(errors))

    def encode(self, frame):
        np.copyto(self.pixels, frame)
        vf = self.reformatter.reformat(self.bgra, format=self.pix_fmt, threads=0)
        vf.pts = self.pts
        self.pts += 1
        return [(bytes(p), p.is_keyframe) for p in self.ctx.encode(vf)]


def h264_codec_string(annexb):
    """WebCodecs codec string (avc1.PPCCLL) read from the SPS in a keyframe."""
    i = 0
    while (i := annexb.find(b"\x00\x00\x01", i)) >= 0:
        i += 3
        if annexb[i] & 0x1F == 7:
            return "avc1." + annexb[i + 1:i + 4].hex()
    raise ValueError("no SPS in H.264 keyframe")


# ---------------------------------------------------------------- displays & state

cfg = load_json(CONFIG_PATH, {})
displays = list_displays()
primary_display, extend_display = pick_displays(displays, cfg.get("extend_display"))


def open_camera(display):
    device_idx, output_idx = display["dxgi"]
    return dxcam.create(device_idx=device_idx, output_idx=output_idx, output_color="BGRA")


SOURCES = {"duplicate": (primary_display, open_camera(primary_display))}  # mirror of the laptop screen
if extend_display:
    SOURCES["extend"] = (extend_display, open_camera(extend_display))  # the virtual monitor

jpeg_quality = cfg.get("jpeg_quality", 70)
h264_quality = cfg.get("h264_quality", 23)
# Frames sent but not yet decoded on the iPad. On a high-latency WiFi link, allowing only one
# or two would cap the frame rate at ~2 per round trip; small desktop frames can safely overlap
# more, while the byte cap stops big frames (video, keyframes) from queueing up.
H264_MAX_IN_FLIGHT = 4
H264_MAX_IN_FLIGHT_BYTES = 256 * 1024

cond = threading.Condition()
preferred_mode = load_json(STATE_PATH, {}).get("mode", "extend")
mode = preferred_mode if preferred_mode in SOURCES else "duplicate"
monitor, cam = SOURCES[mode]
source_gen = 0  # bumped on every mode switch
latest_frame = None
frame_version = 0
waiting = 0  # streams currently waiting for a newer frame
POLL_S = 0.001  # a DXGI poll with no new frame costs ~0.05 ms, so poll often for low latency


def set_mode(new_mode):
    global mode, monitor, cam, source_gen, latest_frame, frame_version
    if new_mode not in SOURCES:
        return
    STATE_PATH.write_text(json.dumps({"mode": new_mode}), encoding="utf-8")  # survives restarts
    with cond:
        if new_mode == mode:
            return
        mode = new_mode
        monitor, cam = SOURCES[mode]
        source_gen += 1
        latest_frame = None
        frame_version += 1
        cond.notify_all()


def capture_loop():
    """Poll the display only while a stream waits for a new frame: no capture work while
    encoders are busy, and a change on screen is picked up within ~1 ms."""
    global latest_frame, frame_version
    have_frame_for = -1  # source_gen we already have a full frame for
    failing = False
    while True:
        with cond:
            cond.wait_for(lambda: waiting > 0)
            gen, src_cam = source_gen, cam
        # Normally only changed frames (None if nothing changed); right after start-up or a
        # mode switch, take the current image even if the screen is static.
        try:
            new = src_cam.grab(new_frame_only=have_frame_for == gen)
        except Exception as e:
            # e.g. the display is being reconfigured; the display watcher restarts us if needed
            if not failing:
                print(f"Capture of the {mode} display failed ({e}); retrying.", flush=True)
                failing = True
            time.sleep(1)
            continue
        failing = False
        if new is None:
            time.sleep(POLL_S)
            continue
        with cond:
            if gen == source_gen:
                latest_frame = new
                have_frame_for = gen
                frame_version += 1
                cond.notify_all()


def watch_displays():
    """Restart (via the supervisor) when displays change: Win+P, resolution, rotation,
    the virtual display being switched on or off..."""
    baseline = display_signature()
    while True:
        time.sleep(2)
        if display_signature() != baseline:
            print("Display setup changed.", flush=True)
            os._exit(RESTART_EXIT_CODE)


# ---------------------------------------------------------------- input & clipboard

pen = winput.Pen()
input_clients = set()
input_clients_lock = threading.Lock()
clipboard_from_ipad = None  # last text the iPad sent, so it isn't echoed straight back


class InputClient:
    def __init__(self, ws):
        self.ws = ws
        self.lock = threading.Lock()  # the clipboard watcher also sends on this socket

    def send(self, obj):
        with self.lock:
            self.ws.send(json.dumps(obj))


def broadcast(obj):
    with input_clients_lock:
        clients = list(input_clients)
    for client in clients:
        try:
            client.send(obj)
        except Exception:
            pass


def watch_clipboard():
    """Push text copied in Windows to the iPad (it lands in the iPad clipboard on the next tap)."""
    seq = winput.clipboard_sequence()
    while True:
        time.sleep(0.5)
        new_seq = winput.clipboard_sequence()
        if new_seq == seq:
            continue
        seq = new_seq
        try:
            text = winput.get_clipboard_text()
        except OSError:
            continue
        if text and text != clipboard_from_ipad and len(text) <= 1_000_000:
            broadcast({"clipboard": text})


CURSOR_POLL_S = 0.008  # ~120 updates/s, only sent when the cursor actually changes
cursor_state = None  # last {"x", "y", "id"} sent (x/y as fractions of the display), or None if hidden/elsewhere


def cursor_loop():
    """Tell the iPads where the cursor is and which shape it has; they draw it themselves."""
    global cursor_state
    while True:
        time.sleep(CURSOR_POLL_S)
        with input_clients_lock:
            if not input_clients:
                continue
        with cond:
            mon = monitor
        cur = cursor_on(mon)
        if cur is None:
            state = None
        else:
            x, y, handle = cur
            shape = cursor_shape(handle)
            state = {"x": x / mon["width"], "y": y / mon["height"], "id": shape and shape["id"]}
        if state != cursor_state:
            cursor_state = state
            broadcast({"cursor": state})


def to_screen_coords(nx, ny):
    x = monitor["left"] + int(nx * monitor["width"])
    y = monitor["top"] + int(ny * monitor["height"])
    return x, y


def handle_input(data):
    """Apply one message from the iPad; returns a reply for it, if any."""
    global clipboard_from_ipad
    kind = data.get("type")

    if kind in ("move", "down", "up"):
        winput.mouse_move(*to_screen_coords(data["x"], data["y"]))
        if kind != "move":
            winput.mouse_button(data.get("button", "left"), down=kind == "down")

    elif kind in ("wheel", "hwheel"):
        winput.mouse_wheel(data.get("delta", 0), horizontal=kind == "hwheel", ctrl=bool(data.get("ctrl")))

    elif kind == "pen":
        x, y = to_screen_coords(data["x"], data["y"])
        phase = data["phase"]
        if not pen.inject(phase, x, y, data.get("pressure", 0.5), data.get("tiltX", 0), data.get("tiltY", 0)):
            winput.mouse_move(x, y)  # no pen support in this Windows: behave like a mouse
            if phase in ("down", "up"):
                winput.mouse_button("left", down=phase == "down")

    elif kind == "char":
        winput.type_char(data.get("char", ""))

    elif kind == "key":
        winput.tap_named_key(data.get("name"))

    elif kind in ("keydown", "keyup") and data.get("code") in winput.SCANCODES:
        if kind == "keydown" and "caps" in data:
            winput.sync_caps_lock(bool(data["caps"]))
        winput.send_scancode(data["code"], up=kind == "keyup")

    elif kind == "caps":
        winput.sync_caps_lock(bool(data.get("on")))

    elif kind == "start":
        winput.open_start_menu()

    elif kind == "mode":
        set_mode(data.get("mode"))
        return {"mode": mode}  # tells the iPad if the switch was refused

    elif kind == "orientation":
        # Turn the virtual monitor to match the iPad; the display watcher then restarts
        # the server so capture and the stream pick up the new size.
        if mode == "extend" and cfg.get("auto_rotate", True):
            error = set_portrait(monitor["name"], bool(data.get("portrait")))
            if error:
                return {"notice": f"Rotasi gagal: {error}"}

    elif kind == "clipboard":
        clipboard_from_ipad = data.get("text", "")
        winput.set_clipboard_text(clipboard_from_ipad)

    elif kind == "ping":
        return {"pong": data.get("t")}

    elif kind == "cursor_shape":
        with cursor_shapes_lock:
            shape = next((s for s in cursor_shapes.values() if s and s["id"] == data.get("id")), None)
        if shape:
            return {"cursor_shape": shape}

    return None


# ---------------------------------------------------------------- web app

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # page edits apply on reload, without restarting the server
sock = Sock(app)
audio = AudioBroadcaster()


def set_nodelay(ws):
    ws.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def current_source():
    with cond:
        return source_gen, monitor, mode


def wait_new_frame(sent, gen):
    """Return (frame, version) newer than `sent`; None after a 1s timeout or
    when the source switched (the stream must then restart at the new size)."""
    global waiting
    with cond:
        waiting += 1
        cond.notify_all()  # wakes the capture loop
        try:
            ready = cond.wait_for(
                lambda: source_gen != gen or (latest_frame is not None and frame_version != sent),
                timeout=1.0,
            )
        finally:
            waiting -= 1
        if not ready or source_gen != gen:
            return None
        return latest_frame, frame_version


PAGE_PATH = HERE / "templates" / "index.html"


def page_version():
    """Changes whenever index.html is edited; open iPads reload themselves when it does."""
    return str(PAGE_PATH.stat().st_mtime_ns)


@app.route("/")
def index():
    html = render_template("index.html", https_port=cfg.get("https_port", 5001), mode=mode, version=page_version())
    return html, 200, {"Cache-Control": "no-store"}


@app.route("/ca.crt")
def ca_cert():
    return send_file(CERT_DIR / "ca.crt", mimetype="application/x-x509-ca-cert")


@app.route("/monitors")
def monitors_debug():
    return jsonify({"displays": displays, "duplicate": primary_display, "extend": extend_display})


@sock.route("/ws/video")
def ws_video(ws):
    set_nodelay(ws)
    gen, _, _ = current_source()
    sent = -1
    while ws.connected and source_gen == gen:
        got = wait_new_frame(sent, gen)
        if got is None:
            continue
        frame, sent = got
        ws.send(encode_jpeg(frame, jpeg_quality))
        # The client acks after drawing, so at most one frame is ever in flight
        # and stale frames can't pile up in network buffers.
        ws.receive()


@sock.route("/ws/h264")
def ws_h264(ws):
    set_nodelay(ws)
    gen, mon, src_mode = current_source()
    encoder = H264Encoder(mon["width"], mon["height"], h264_quality)
    sent, configured = -1, False
    in_flight = deque()  # sizes of frames the iPad hasn't acked yet (acks arrive in order)
    while ws.connected and source_gen == gen:
        while in_flight and ws.receive(timeout=0) is not None:
            in_flight.popleft()
        if len(in_flight) >= H264_MAX_IN_FLIGHT or sum(in_flight) >= H264_MAX_IN_FLIGHT_BYTES:
            ws.receive()
            in_flight.popleft()
            continue
        got = wait_new_frame(sent, gen)
        if got is None:
            continue
        frame, sent = got
        if frame.shape[:2] != (encoder.height, encoder.width):
            continue  # display mid-reconfiguration; a restart with the new size follows
        for data, key in encoder.encode(frame):
            if not configured:
                ws.send(json.dumps({
                    "codec": h264_codec_string(data),
                    "width": mon["width"],
                    "height": mon["height"],
                    "encoder": encoder.name,
                    "mode": src_mode,
                }))
                configured = True
            ws.send((b"\x01" if key else b"\x00") + data)
            in_flight.append(len(data))


@sock.route("/ws/input")
def ws_input(ws):
    set_nodelay(ws)
    client = InputClient(ws)
    # An iPad that reconnects after a server restart may still run an older copy of the page.
    client.send({"version": page_version()})
    client.send({"cursor": cursor_state})  # updates are only sent on change
    with input_clients_lock:
        input_clients.add(client)
    try:
        while True:
            reply = handle_input(json.loads(ws.receive()))
            if reply:
                client.send(reply)
    finally:
        with input_clients_lock:
            input_clients.discard(client)


@sock.route("/ws/audio")
def ws_audio(ws):
    set_nodelay(ws)
    audio.add(ws.send)
    try:
        while True:
            ws.receive()  # nothing is expected; this just blocks until the iPad disconnects
    finally:
        audio.remove(ws.send)


camera_lock = threading.Lock()  # the virtual webcam can take only one iPad camera at a time


@sock.route("/ws/camera")
def ws_camera(ws):
    """iPad camera -> Windows webcam. The iPad sends H.264 (Annex B) chunks."""
    set_nodelay(ws)
    if not camera_lock.acquire(blocking=False):
        ws.send(json.dumps({"error": "Kamera iPad lain sedang dipakai sebagai webcam"}))
        return
    try:
        try:
            bridge = CameraBridge()
        except Exception as e:
            print(f"Virtual camera unavailable: {e}", flush=True)
            ws.send(json.dumps({"error": "OBS Virtual Camera tidak bisa dipakai: sedang dipakai program lain "
                                         "(mis. OBS Studio dengan Start Virtual Camera), atau OBS belum terpasang"}))
            return
        ws.send(json.dumps({"ok": bridge.name}))
        try:
            while True:
                data = ws.receive()
                if isinstance(data, bytes):
                    bridge.push(data)
        finally:
            bridge.close()
    finally:
        camera_lock.release()


class TLSServer(ThreadedWSGIServer):
    """Werkzeug's built-in TLS does the handshake inside accept(), so a single client
    that connects but never finishes the handshake (e.g. a Safari preconnect) blocks
    every other connection. Do the handshake in the per-connection thread instead."""

    def __init__(self, host, port, app, ssl_ctx):
        super().__init__(host, port, app)
        self.tls_ctx = ssl_ctx
        self.ssl_context = ssl_ctx  # only so werkzeug reports https:// and logs SSL errors

    def finish_request(self, request, client_address):
        request.settimeout(10)
        # Safari uses http:// when the address is typed without a scheme; a TLS
        # ClientHello always starts with 0x16, so anything else gets redirected.
        try:
            first = request.recv(1, socket.MSG_PEEK)
        except OSError:
            first = b""
        if first and first != b"\x16":
            self._redirect_to_https(request)
            return
        if not first:
            request.close()
            return
        try:
            request = self.tls_ctx.wrap_socket(request, server_side=True)
        except OSError as e:
            self.log("info", f"TLS handshake with {client_address[0]} failed: {e}")
            request.close()
            return
        request.settimeout(None)
        try:
            super().finish_request(request, client_address)
        finally:
            request.close()

    def _redirect_to_https(self, request):
        try:
            head = request.recv(8192).decode("latin-1").split("\r\n")
            parts = head[0].split(" ")
            path = parts[1] if len(parts) > 1 and parts[1].startswith("/") else "/"
            host = next((line.split(":", 1)[1].strip() for line in head[1:]
                         if line.lower().startswith("host:")), None)
            host = host or f"{request.getsockname()[0]}:{self.server_port}"
            request.sendall(
                f"HTTP/1.1 301 Moved Permanently\r\nLocation: https://{host}{path}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n".encode("latin-1")
            )
        except OSError:
            pass
        finally:
            request.close()


def serve(ip, port, https_port):
    crt, key, _ = ensure_certs(CERT_DIR, ip)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(crt, key)
    https_server = TLSServer("0.0.0.0", https_port, app, ssl_ctx)
    http_server = make_server("0.0.0.0", port, app, threaded=True)
    threading.Thread(target=https_server.serve_forever, daemon=True).start()
    http_server.serve_forever()


def describe_displays():
    for d in displays:
        role = "duplicate" if d is primary_display else "extend" if d is extend_display else "-"
        print(f"  {d['name']:<16} {d['width']}x{d['height']} at ({d['left']},{d['top']})  "
              f"{d['adapter']}  [{role}]")
    if not extend_display:
        print("-> No second display found, so only duplicate mode is available. For extend, "
              "set Windows to 'Extend these displays' (it is picked up automatically).")


if __name__ == "__main__":
    for target in (capture_loop, cursor_loop, watch_displays, watch_clipboard):
        threading.Thread(target=target, daemon=True).start()
    ip = get_lan_ip()
    port, https_port = cfg.get("port", 5000), cfg.get("https_port", 5001)
    print("\nDisplays:")
    describe_displays()
    print(f"\nH.264 mode (recommended):  https://{ip}:{https_port}/")
    print(f"JPEG fallback:             http://{ip}:{port}/")
    print(f"First time on the iPad, install the certificate from http://{ip}:{port}/ca.crt\n", flush=True)
    serve(ip, port, https_port)
