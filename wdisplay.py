"""Windows display helpers: finding the virtual monitor, noticing display changes,
rotating a display, and reading/rendering the mouse cursor."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import dxcam
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# ---------------------------------------------------------------- finding displays


def list_displays():
    """Every display DXGI can capture, with its device name, adapter name, desktop
    geometry (physical pixels) and the dxcam indices to capture it."""
    # dxcam enumerates outputs once, when it is imported; this is that same factory.
    factory = dxcam.Singleton._instances[dxcam.DXFactory]
    meta = factory.output_metadata  # {device name: [adapter name, is primary, monitors]}
    displays = []
    for device_idx, outputs in enumerate(factory.outputs):
        for output_idx, output in enumerate(outputs):
            rect = output.desc.DesktopCoordinates
            adapter, primary = meta.get(output.devicename, ("", False))[:2]
            displays.append({
                "name": output.devicename,
                "adapter": adapter,
                "primary": bool(primary),
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
                "dxgi": (device_idx, output_idx),
            })
    return displays


def pick_displays(displays, extend_name=None):
    """(laptop screen, virtual monitor or None). The virtual monitor is the display named
    in config.json, else a non-primary display on a "virtual" adapter, else any non-primary one."""
    primary = next((d for d in displays if d["primary"]), displays[0])
    others = [d for d in displays if d is not primary]
    if extend_name:
        extend = next((d for d in others if d["name"].lower() == extend_name.lower()), None)
    else:
        virtual = [d for d in others if "virtual" in d["adapter"].lower()]
        extend = (virtual or others or [None])[0]
    return primary, extend


# ---------------------------------------------------------------- noticing changes

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORENUMPROC, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]


def display_signature():
    """Cheap snapshot of which displays exist and where; changes on Win+P, resolution
    changes, rotation, or a display being added/removed."""
    found = []

    def on_monitor(hmonitor, hdc, rect, data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
        r = info.rcMonitor
        found.append((info.szDevice, r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(on_monitor), 0)
    return tuple(sorted(found))


# ---------------------------------------------------------------- rotation


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


ENUM_CURRENT_SETTINGS = -1
DM_DISPLAYORIENTATION = 0x00000080
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DMDO_DEFAULT = 0
DMDO_90 = 1
user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
user32.ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW), wintypes.HWND, wintypes.DWORD, wintypes.LPVOID
]
user32.ChangeDisplaySettingsExW.restype = ctypes.c_long


def set_portrait(device_name, portrait):
    """Rotate a display to portrait or back to landscape (until the next reboot).
    Returns an error message, or None when it worked or nothing needed to change."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return "tidak bisa membaca pengaturan display"
    if (dm.dmPelsHeight > dm.dmPelsWidth) == portrait:
        return None
    dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth
    dm.dmDisplayOrientation = DMDO_90 if portrait else DMDO_DEFAULT
    dm.dmFields |= DM_DISPLAYORIENTATION | DM_PELSWIDTH | DM_PELSHEIGHT
    # Flags 0: change it now without saving it to the registry.
    result = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None, 0, None)
    return None if result == 0 else f"Windows menolak rotasi (kode {result})"


# ---------------------------------------------------------------- the mouse cursor

CURSOR_SHOWING = 0x00000001
DI_NORMAL = 0x0003


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int,
    wintypes.UINT, wintypes.HBRUSH, wintypes.UINT,
]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]


def cursor_on(display):
    """(x, y, cursor handle) relative to `display`, or None if the cursor is hidden or elsewhere."""
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetCursorInfo(ctypes.byref(info)) or not (info.flags & CURSOR_SHOWING):
        return None
    x = info.ptScreenPos.x - display["left"]
    y = info.ptScreenPos.y - display["top"]
    if 0 <= x < display["width"] and 0 <= y < display["height"]:
        return x, y, info.hCursor or 0
    return None


@dataclass
class CursorImage:
    hot_x: int
    hot_y: int
    color: np.ndarray  # (h, w, 3) BGR, already multiplied by alpha
    alpha: np.ndarray  # (h, w, 1) float 0..1
    invert: np.ndarray  # (h, w) bool: pixels that invert the screen (e.g. the I-beam)


def _draw_icon(handle, w, h, background):
    hdc = gdi32.CreateCompatibleDC(None)
    header = BITMAPINFOHEADER(
        biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32
    )
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, bitmap)
    try:
        pixels = np.ctypeslib.as_array((ctypes.c_uint8 * (w * h * 4)).from_address(bits.value)).reshape(h, w, 4)
        pixels[:] = background
        user32.DrawIconEx(hdc, 0, 0, handle, w, h, 0, None, DI_NORMAL)
        gdi32.GdiFlush()
        return pixels.copy()
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc)


def render_cursor(handle):
    """Rasterize a Windows cursor so it can be painted into captured frames (the capture
    never contains the hardware cursor). Returns None if Windows won't describe it."""
    info = ICONINFO()
    if not handle or not user32.GetIconInfo(handle, ctypes.byref(info)):
        return None
    try:
        bm = BITMAP()
        gdi32.GetObjectW(info.hbmMask, ctypes.sizeof(bm), ctypes.byref(bm))
        w = bm.bmWidth
        # Monochrome cursors stack their AND and XOR masks in one double-height bitmap.
        h = bm.bmHeight if info.hbmColor else bm.bmHeight // 2
    finally:
        gdi32.DeleteObject(info.hbmMask)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
    if w <= 0 or h <= 0:
        return None
    # Drawing on black and on white recovers transparency: opaque pixels look the same on
    # both, transparent ones show the background, inverting ones swap black and white.
    on_black = _draw_icon(handle, w, h, 0)[..., :3].astype(np.int16)
    on_white = _draw_icon(handle, w, h, 255)[..., :3].astype(np.int16)
    diff = on_white - on_black
    invert = diff.min(axis=2) < -128
    alpha = np.clip(255 - diff.max(axis=2), 0, 255).astype(np.float32) / 255
    alpha[invert] = 0
    return CursorImage(
        hot_x=info.xHotspot,
        hot_y=info.yHotspot,
        color=np.where(invert[..., None], 0, on_black).astype(np.float32),
        alpha=alpha[..., None],
        invert=invert,
    )
