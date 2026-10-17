"""Streams whatever the laptop is playing (WASAPI loopback) to listening iPads as raw
16-bit stereo PCM, 20 ms per message (~1.5 Mbit/s, trivial on a LAN)."""
import ctypes
import threading
import time
import warnings

import numpy as np
import soundcard

RATE = 48000
CHANNELS = 2
CHUNK = RATE // 50  # 20 ms

# soundcard warns on every tiny gap in the loopback stream (e.g. while nothing plays).
warnings.filterwarnings("ignore", message=".*discontinuity.*")


class AudioBroadcaster:
    def __init__(self):
        self._listeners = set()
        self._cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def add(self, send):
        with self._cond:
            self._listeners.add(send)
            self._cond.notify_all()

    def remove(self, send):
        with self._cond:
            self._listeners.discard(send)

    def _run(self):
        # WASAPI is COM; soundcard only initializes COM on the thread that imported it.
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._listeners)
            try:
                self._record_while_listened()
            except Exception as e:
                print(f"Audio capture failed ({e}); retrying...", flush=True)
                time.sleep(2)

    def _record_while_listened(self):
        # Only record while someone listens: a recorder left idle would buffer stale audio.
        speaker = soundcard.default_speaker()
        loopback = soundcard.get_microphone(id=str(speaker.name), include_loopback=True)
        with loopback.recorder(samplerate=RATE, channels=CHANNELS, blocksize=CHUNK) as recorder:
            while True:
                with self._cond:
                    listeners = list(self._listeners)
                if not listeners:
                    return
                samples = recorder.record(numframes=CHUNK)
                pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                for send in listeners:
                    try:
                        send(pcm)
                    except Exception:
                        self.remove(send)
