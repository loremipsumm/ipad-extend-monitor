"""iPad camera -> Windows webcam: decodes the H.264 stream the iPad's camera page sends and
feeds it to the OBS Virtual Camera, which Zoom/Meet/Teams list as "OBS Virtual Camera"."""
import av
import numpy as np
import pyvirtualcam
from av.video.reformatter import VideoReformatter

OUT_W, OUT_H, OUT_FPS = 1280, 720, 30


class CameraBridge:
    def __init__(self):
        # Raises if the OBS Virtual Camera isn't installed (it comes with OBS Studio).
        self.cam = pyvirtualcam.Camera(width=OUT_W, height=OUT_H, fps=OUT_FPS, fmt=pyvirtualcam.PixelFormat.RGB)
        self.name = self.cam.device
        self.decoder = av.CodecContext.create("h264", "r")
        self.reformatter = VideoReformatter()
        self.canvas = np.zeros((OUT_H, OUT_W, 3), np.uint8)

    def push(self, annexb):
        try:
            frames = self.decoder.decode(av.Packet(annexb))
        except av.error.InvalidDataError:
            return  # a damaged chunk; the next keyframe recovers
        for frame in frames:
            self._send(frame)

    def _send(self, frame):
        # Fit inside 1280x720 without stretching: a portrait iPad gets black bars.
        scale = min(OUT_W / frame.width, OUT_H / frame.height)
        w = max(2, int(frame.width * scale) // 2 * 2)
        h = max(2, int(frame.height * scale) // 2 * 2)
        rgb = self.reformatter.reformat(frame, width=w, height=h, format="rgb24", threads=0).to_ndarray()
        if (w, h) != (OUT_W, OUT_H):
            self.canvas[:] = 0
            x, y = (OUT_W - w) // 2, (OUT_H - h) // 2
            self.canvas[y:y + h, x:x + w] = rgb
            rgb = self.canvas
        self.cam.send(rgb)

    def close(self):
        self.cam.close()
