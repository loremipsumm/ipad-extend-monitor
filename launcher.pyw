"""Tray launcher: double-click to run the server in the background, with an icon in the
Windows notification area to copy the iPad address, open the log, restart or quit."""
import ctypes
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
LOG_PATH = HERE / "server.log"
NO_WINDOW = subprocess.CREATE_NO_WINDOW
server = None


def https_port():
    try:
        return json.loads((HERE / "config.json").read_text(encoding="utf-8-sig")).get("https_port", 5001)
    except (OSError, ValueError):
        return 5001


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server(fresh_log):
    global server
    with open(LOG_PATH, "w" if fresh_log else "a", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, str(HERE / "server.py")],
            cwd=HERE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=NO_WINDOW,
        )


def stop_server():
    if server and server.poll() is None:
        # server.py supervises a child process, so take down the whole tree.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)], capture_output=True, creationflags=NO_WINDOW)
        server.wait()


def make_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((6, 12, 58, 52), radius=6, fill=(40, 40, 40, 255), outline=(255, 255, 255, 255), width=3)
    draw.rectangle((14, 19, 50, 45), fill=(70, 150, 255, 255))
    return img


def main():
    port = https_port()
    if port_in_use(port):
        ctypes.windll.user32.MessageBoxW(None, f"Server sudah berjalan di port {port}.", "iPad Extend", 0x40)
        return
    url = f"https://{lan_ip()}:{port}/"
    start_server(fresh_log=True)

    def copy_url(icon, item):
        subprocess.run(["clip"], input=url.encode("ascii"), creationflags=NO_WINDOW)
        icon.notify(f"{url} disalin. Buka di Safari iPad.", "iPad Extend")

    def open_log(icon, item):
        os.startfile(LOG_PATH)

    def restart(icon, item):
        stop_server()
        start_server(fresh_log=False)
        icon.notify("Server di-restart.", "iPad Extend")

    def quit_app(icon, item):
        stop_server()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"Salin alamat iPad ({url})", copy_url, default=True),
        pystray.MenuItem("Buka log", open_log),
        pystray.MenuItem("Restart server", restart),
        pystray.MenuItem("Keluar", quit_app),
    )
    pystray.Icon("ipad-extend", make_icon(), f"iPad Extend - {url}", menu).run()


if __name__ == "__main__":
    main()
