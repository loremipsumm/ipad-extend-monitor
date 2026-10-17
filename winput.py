"""Injecting input into Windows: keyboard, mouse, pen (Apple Pencil) and the clipboard."""
import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- keyboard & mouse

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
}
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_TAB = 0x09
VK_CAPITAL = 0x14
NAMED_VKS = {"Enter": VK_RETURN, "Backspace": VK_BACK, "Tab": VK_TAB}

# KeyboardEvent.code (physical key) -> (scan code set 1, extended). Sending scan codes
# rather than characters makes shortcuts, key repeat and games behave like a real keyboard.
SCANCODES = {
    code: (0x02 + i, False)
    for i, code in enumerate((
        "Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0 Minus Equal Backspace Tab "
        "KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight Enter ControlLeft "
        "KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote Backquote ShiftLeft Backslash "
        "KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash ShiftRight NumpadMultiply AltLeft Space "
        "CapsLock F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 NumLock ScrollLock Numpad7 Numpad8 Numpad9 NumpadSubtract "
        "Numpad4 Numpad5 Numpad6 NumpadAdd Numpad1 Numpad2 Numpad3 Numpad0 NumpadDecimal"
    ).split())
}
SCANCODES.update({
    "Escape": (0x01, False), "IntlBackslash": (0x56, False), "F11": (0x57, False), "F12": (0x58, False),
    "NumLock": (0x45, True), "NumpadEnter": (0x1C, True), "ControlRight": (0x1D, True),
    "NumpadDivide": (0x35, True), "PrintScreen": (0x37, True), "AltRight": (0x38, True),
    "Home": (0x47, True), "ArrowUp": (0x48, True), "PageUp": (0x49, True), "ArrowLeft": (0x4B, True),
    "ArrowRight": (0x4D, True), "End": (0x4F, True), "ArrowDown": (0x50, True), "PageDown": (0x51, True),
    "Insert": (0x52, True), "Delete": (0x53, True), "ContextMenu": (0x5D, True),
    # Cmd acts as Ctrl, so Mac-style shortcuts (Cmd+C/V/Z/S/A) do the Windows equivalent.
    "MetaLeft": (0x1D, False), "MetaRight": (0x1D, True),
})


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        # mi is unused, but it is the union's largest member: without it sizeof(INPUT)
        # is wrong and SendInput rejects every call.
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _anonymous_ = ("_i",)
    _fields_ = [("type", ctypes.c_ulong), ("_i", _I)]


def _send_key(vk, scan, flags):
    extra = ctypes.c_ulong(0)
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, ctypes.pointer(extra)))
    user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def type_char(ch):
    _send_key(0, ord(ch), KEYEVENTF_UNICODE)
    _send_key(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def tap_named_key(name):
    vk = NAMED_VKS.get(name)
    if vk:
        _send_key(vk, 0, 0)
        _send_key(vk, 0, KEYEVENTF_KEYUP)


def send_scancode(code, up):
    scancode, extended = SCANCODES[code]
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
    _send_key(0, scancode, flags)


@contextmanager
def held(code):
    send_scancode(code, up=False)
    try:
        yield
    finally:
        send_scancode(code, up=True)


def sync_caps_lock(on):
    """Match Windows' Caps Lock to the iPad's, so letters come out in the same case."""
    if bool(user32.GetKeyState(VK_CAPITAL) & 1) != on:
        send_scancode("CapsLock", up=False)
        send_scancode("CapsLock", up=True)


def open_start_menu():
    # Ctrl+Esc opens Start like a Windows-key tap, but unlike an injected Windows key
    # it isn't ignored by the shell.
    with held("ControlLeft"):
        send_scancode("Escape", up=False)
        send_scancode("Escape", up=True)


def mouse_move(x, y):
    user32.SetCursorPos(x, y)


def mouse_button(button, down):
    down_flag, up_flag = BUTTON_FLAGS[button]
    user32.mouse_event(down_flag if down else up_flag, 0, 0, 0, 0)


def mouse_wheel(delta, horizontal=False, ctrl=False):
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    if ctrl:  # Ctrl+wheel = zoom in most apps (pinch gesture)
        with held("ControlLeft"):
            user32.mouse_event(flag, 0, 0, int(delta), 0)
    else:
        user32.mouse_event(flag, 0, 0, int(delta), 0)


# ---------------------------------------------------------------- pen (Apple Pencil)

PT_PEN = 3
POINTER_FEEDBACK_DEFAULT = 1
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
PEN_MASK_PRESSURE = 0x1
PEN_MASK_TILT_X = 0x4
PEN_MASK_TILT_Y = 0x8
PEN_PHASE_FLAGS = {
    "down": POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN,
    "move": POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_UPDATE,
    "hover": POINTER_FLAG_INRANGE | POINTER_FLAG_UPDATE,
    "up": POINTER_FLAG_INRANGE | POINTER_FLAG_UP,
    "leave": POINTER_FLAG_UPDATE,  # out of range: Windows stops showing the pen cursor
}


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", ctypes.c_uint32),
        ("pointerId", ctypes.c_uint32),
        ("frameId", ctypes.c_uint32),
        ("pointerFlags", ctypes.c_uint32),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", wintypes.POINT),
        ("ptHimetricLocation", wintypes.POINT),
        ("ptPixelLocationRaw", wintypes.POINT),
        ("ptHimetricLocationRaw", wintypes.POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", ctypes.c_uint32),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", ctypes.c_int),
    ]


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("penFlags", ctypes.c_uint32),
        ("penMask", ctypes.c_uint32),
        ("pressure", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("tiltX", ctypes.c_int32),
        ("tiltY", ctypes.c_int32),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("touchFlags", ctypes.c_uint32),
        ("touchMask", ctypes.c_uint32),
        ("rcContact", wintypes.RECT),
        ("rcContactRaw", wintypes.RECT),
        ("orientation", ctypes.c_uint32),
        ("pressure", ctypes.c_uint32),
    ]


class POINTER_TYPE_INFO(ctypes.Structure):
    class _U(ctypes.Union):
        # touchInfo is unused but sizes the union like the real Windows struct.
        _fields_ = [("touchInfo", POINTER_TOUCH_INFO), ("penInfo", POINTER_PEN_INFO)]

    _anonymous_ = ("_u",)
    _fields_ = [("type", ctypes.c_uint32), ("_u", _U)]


user32.CreateSyntheticPointerDevice.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
user32.CreateSyntheticPointerDevice.restype = wintypes.HANDLE
user32.InjectSyntheticPointerInput.argtypes = [wintypes.HANDLE, ctypes.POINTER(POINTER_TYPE_INFO), ctypes.c_uint32]
user32.InjectSyntheticPointerInput.restype = wintypes.BOOL


class Pen:
    """A synthetic Windows pen, so drawing apps get real pressure and tilt from the Apple Pencil."""

    def __init__(self):
        self._device = None

    def inject(self, phase, x, y, pressure=0.0, tilt_x=0, tilt_y=0):
        """Returns False when Windows can't inject pen input (caller falls back to the mouse)."""
        if self._device is None:
            self._device = user32.CreateSyntheticPointerDevice(PT_PEN, 1, POINTER_FEEDBACK_DEFAULT) or 0
        if not self._device:
            return False
        ok = self._inject(PEN_PHASE_FLAGS[phase], x, y, pressure, tilt_x, tilt_y)
        if ok and phase == "up":
            ok = self._inject(PEN_PHASE_FLAGS["leave"], x, y, 0.0, 0, 0)
        return ok

    def _inject(self, flags, x, y, pressure, tilt_x, tilt_y):
        info = POINTER_TYPE_INFO(type=PT_PEN)
        pen = info.penInfo
        pen.pointerInfo.pointerType = PT_PEN
        pen.pointerInfo.pointerFlags = flags
        pen.pointerInfo.ptPixelLocation = wintypes.POINT(x, y)
        pen.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
        pen.pressure = max(0, min(1024, int(pressure * 1024)))
        pen.tiltX = max(-90, min(90, int(tilt_x)))
        pen.tiltY = max(-90, min(90, int(tilt_y)))
        return bool(user32.InjectSyntheticPointerInput(self._device, ctypes.byref(info), 1))


# ---------------------------------------------------------------- clipboard

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
HWND_MESSAGE = -3

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]


@contextmanager
def _open_clipboard(hwnd=None):
    for _ in range(25):  # another app may be holding it for a moment
        if user32.OpenClipboard(hwnd):
            break
        time.sleep(0.02)
    else:
        raise OSError("clipboard is busy")
    try:
        yield
    finally:
        user32.CloseClipboard()


def clipboard_sequence():
    """Changes every time anything is copied in Windows."""
    return user32.GetClipboardSequenceNumber()


def get_clipboard_text():
    with _open_clipboard():
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)


def set_clipboard_text(text):
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    # SetClipboardData fails when the clipboard was opened without an owner window,
    # so borrow a throwaway message-only window.
    hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
    try:
        with _open_clipboard(hwnd):
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                raise OSError("SetClipboardData failed")
    finally:
        user32.DestroyWindow(hwnd)
