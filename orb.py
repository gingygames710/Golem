"""Golem Orb - an ambient presence for your desktop (1.0).

A frameless, always-on-top, click-through glowing orb: a ball of floating data with 1s and 0s
orbiting it in pseudo-3D. It breathes gently on its own and brightens when Golem is active.

Built for speed: a small translucent window (so the repaint area is tiny), a cached glow pixmap, and
a cheap per-frame 3D projection of a fixed set of digits. Runs light even on weak hardware.

- Right-click the tray icon (or the orb when unlocked) for the menu: Appearance, Lock/Unlock, Quit.
- It's click-through by default, so it never gets in the way; Unlock it to drag it somewhere.
- Everything about how it looks lives in the config under "orb" and is fully themeable.
"""

import json
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPixmap, QRadialGradient, QPen, QAction, QCursor)
from PySide6.QtWidgets import (QApplication, QWidget, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout,
                               QHBoxLayout, QGridLayout, QLabel, QPushButton, QSlider, QColorDialog)

if getattr(sys, "frozen", False):
    AGENT_DIR = Path(sys.executable).resolve().parent
else:
    AGENT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = AGENT_DIR / "agent_config.json"
ICON = AGENT_DIR / "icon.ico"
IS_WINDOWS = os.name == "nt"

# "Twilight" - Claude's own pick: deep blue glow, near-white hot core, cool cyan data, warm amber sparks.
DEFAULT_ORB = {
    "theme": "Twilight",
    "core": "#eaf1ff",    # the hot centre
    "glow": "#5566ff",    # the surrounding glow / halo
    "digit": "#86e8ff",   # the 1s and 0s
    "spark": "#ffb473",   # the occasional digit that flares warm
    "size": 74,           # core radius in px (the window sizes itself to fit)
    "density": 120,       # how many digits orbit
    "speed": 70,          # rotation speed
    "opacity": 96,        # whole-orb opacity, 40-100
    "follow": False,      # drift toward whatever window you're working in
    "face": False,        # the avatar awakens: two eyes of light that look at you and blink
    "x": -1, "y": -1,     # saved position (-1 = bottom-right on first run)
}
ORB_THEMES = {
    "Twilight": {"core": "#eaf1ff", "glow": "#5566ff", "digit": "#86e8ff", "spark": "#ffb473"},
    "Matrix":   {"core": "#eaffea", "glow": "#16a34a", "digit": "#4ade80", "spark": "#bbf7d0"},
    "Ember":    {"core": "#fff0e6", "glow": "#ff5a1f", "digit": "#ffb46b", "spark": "#ffe3b0"},
    "Ice":      {"core": "#ffffff", "glow": "#38bdf8", "digit": "#bae6fd", "spark": "#e0f2fe"},
    "Void":     {"core": "#f3e8ff", "glow": "#a855f7", "digit": "#d8b4fe", "spark": "#f5a7f0"},
    "Rose":     {"core": "#fff0f4", "glow": "#e85d8a", "digit": "#ff9ec0", "spark": "#ffd27f"},
    "Ultron":   {"core": "#ffecec", "glow": "#c1121f", "digit": "#ff4d4d", "spark": "#ff8c3b"},
}


def _config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def load_orb() -> dict:
    ap = dict(DEFAULT_ORB)
    saved = _config().get("orb") or {}
    ap.update({k: v for k, v in saved.items() if k in DEFAULT_ORB and v is not None})
    return ap


def save_orb(ap: dict):
    try:
        cfg = _config()
        cfg["orb"] = ap
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


class Digit:
    """One 1 or 0 on the surface of the data sphere, at a fixed spot that the whole sphere rotates."""
    __slots__ = ("theta", "u", "rr", "char", "phase", "warm")

    def __init__(self):
        self.theta = random.uniform(0, 2 * math.pi)        # longitude (spins around the vertical axis)
        self.u = random.uniform(-1, 1)                     # height on the axis; even over the full sphere
        self.rr = random.uniform(0.82, 1.16)               # shell thickness
        self.char = random.choice("01")
        self.phase = random.uniform(0, 2 * math.pi)        # twinkle offset
        self.warm = random.random() < 0.12                 # a few digits flare in the spark colour


class Orb(QWidget):
    def __init__(self):
        super().__init__()
        self.ap = load_orb()
        self.locked = True                 # click-through (can't be dragged) until unlocked from the menu
        self._drag = None
        self._active_until = 0.0           # brighten the orb until this time (set when Golem is active)
        self._speak_env = []               # per-bucket loudness (0..1) of the voice currently playing
        self._speak_start = 0.0            # monotonic time the voice began
        self._speak_interval = 0.06        # seconds per envelope bucket
        self._speak_until = 0.0            # monotonic time the voice ends - orb pulses with light until then
        self._listen_until = 0.0           # monotonic time Golem stops listening - orb shows a cool ring
        # --- cohesion (1.9.0): one "energy" value, eased every frame, that ALL the motion/colour/face flow
        # from, so the orb reads as a single living thing with moods instead of a pile of separate effects.
        self.energy = 0.12                 # 0 (calm/idle) .. 1 (fully engaged/speaking); eased toward a target
        self._look = [0.0, 0.0]            # where the eyes are looking (eased)
        self._look_target = [0.0, 0.0]     # where they want to look
        self._next_look = 0.0              # when to pick a new idle glance
        self._next_blink = 0.0             # when the next blink happens
        self._blink_start = -9.0           # monotonic time the current blink began
        self._glow = None                  # cached glow pixmap
        self._glow_key = None
        self._fonts = {}                   # size -> QFont cache, so we don't rebuild fonts each frame
        self.digits = [Digit() for _ in range(int(self.ap["density"]))]
        self._t0 = time.monotonic()
        self._last_flip = 0.0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._resize_to_orb()
        self._place()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)               # ~60 fps; the window is small so this stays cheap

        # The agent window writes an "active until" epoch here when Golem is responding/speaking, so the
        # orb can brighten in time with it. Cheap: a tiny file polled a few times a second.
        self._signal = Path(tempfile.gettempdir()) / "golem_orb_signal"
        self._sig_timer = QTimer(self)
        self._sig_timer.timeout.connect(self._check_signal)
        self._sig_timer.start(90)          # poll often enough that the speech pulse starts in time with the voice
        QTimer.singleShot(500, lambda: self.pulse(2.2))   # a warm hello when the orb first appears
        self.face = bool(self.ap.get("face", False))       # the avatar: eyes of light that look at you
        self.follow = bool(self.ap.get("follow", False))   # drift toward the window you're working in
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_tick)
        if self.follow:
            self._follow_timer.start(16)   # 60fps so following is buttery smooth

    # ---- geometry ----
    def _win_half(self) -> int:
        # Must clear the LARGEST the orb ever draws, or the glow gets clipped by the (transparent) window
        # edge and you see a hard square. Peak = glow(size*4) * max breathe(~1.36) * max bloom(~1.20) / 2
        # = size*~3.3; size*3.4 leaves a margin so the halo always fades out inside the window.
        return int(self.ap["size"] * 3.4) + 20

    def _resize_to_orb(self):
        s = self._win_half() * 2
        self.resize(s, s)

    def _place(self):
        s = self.size().width()
        scr = QApplication.primaryScreen().availableGeometry()
        if self.ap["x"] < 0 or self.ap["y"] < 0:
            self.move(scr.right() - s - 24, scr.bottom() - s - 24)
        else:
            # Clamp a saved position so the (now larger) window can't end up off-screen after an update.
            x = max(scr.left(), min(int(self.ap["x"]), scr.right() - s))
            y = max(scr.top(), min(int(self.ap["y"]), scr.bottom() - s))
            self.move(x, y)

    # ---- click-through (Windows) ----
    def showEvent(self, e):
        super().showEvent(e)
        self._apply_click_through()

    def _apply_click_through(self):
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
            WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            if self.locked:
                ex |= WS_EX_TRANSPARENT
            else:
                ex &= ~WS_EX_TRANSPARENT
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    def set_locked(self, locked: bool):
        self.locked = locked
        self._apply_click_through()

    # ---- interaction (only when unlocked) ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            self._menu().exec(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self.ap["x"], self.ap["y"] = self.x(), self.y()
            save_orb(self.ap)
            self._drag = None

    def _menu(self) -> QMenu:
        m = QMenu()
        talk = QAction("Talk to Golem", m)
        talk.triggered.connect(self._request_talk)
        follow = QAction(("Stop following windows" if self.follow else "Follow my windows"), m)
        follow.triggered.connect(self._toggle_follow)
        face = QAction(("Hide face" if self.face else "Show face"), m)
        face.triggered.connect(self._toggle_face)
        lock = QAction("Lock in place" if not self.locked else "Unlock to move", m)
        lock.triggered.connect(lambda: self.set_locked(not self.locked))
        appr = QAction("Appearance...", m)
        appr.triggered.connect(self.open_appearance)
        pulse = QAction("Pulse (test)", m)
        pulse.triggered.connect(lambda: self.pulse(1.5))
        quit_a = QAction("Quit", m)
        quit_a.triggered.connect(QApplication.quit)
        m.addAction(talk)
        m.addAction(follow)
        m.addAction(face)
        m.addSeparator()
        m.addAction(lock)
        m.addAction(appr)
        m.addAction(pulse)
        m.addSeparator()
        m.addAction(quit_a)
        return m

    def _request_talk(self):
        """Ask the chat window to start/stop a voice conversation (it polls this file)."""
        try:
            (Path(tempfile.gettempdir()) / "golem_orb_request").write_text(str(time.time()))
            self.pulse(1.0)
        except Exception:
            pass

    def _toggle_face(self):
        self.face = not self.face
        self.ap["face"] = self.face
        save_orb(self.ap)
        self.pulse(1.2)

    def _toggle_follow(self):
        self.follow = not self.follow
        self.ap["follow"] = self.follow
        save_orb(self.ap)
        if self.follow:
            self._follow_timer.start(16)   # 60fps so following is buttery smooth
            self.pulse(1.2)
        else:
            self._follow_timer.stop()

    def _follow_tick(self):
        """Gently drift the orb toward the top-right of whatever window is in front (Windows only)."""
        if not self.follow or not IS_WINDOWS:
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            if not hwnd or int(hwnd) == int(self.winId()):
                return

            class _R(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                            ("r", ctypes.c_long), ("b", ctypes.c_long)]
            rect = _R()
            if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
                return
            half = self._win_half()
            # aim the orb's centre just inside the active window's top-right corner
            tcx, tcy = rect.r - 36, rect.t + 70
            tx, ty = tcx - half, tcy - half
        except Exception:
            return
        scr = QApplication.primaryScreen().availableGeometry()
        tx = max(scr.left(), min(int(tx), scr.right() - self.width()))
        ty = max(scr.top(), min(int(ty), scr.bottom() - self.height()))
        nx = self.x() + (tx - self.x()) * 0.14   # gentle ease at 30fps -> smooth glide, not a jump
        ny = self.y() + (ty - self.y()) * 0.14
        if abs(nx - self.x()) >= 1 or abs(ny - self.y()) >= 1:   # only move on a real change (less jitter)
            self.move(round(nx), round(ny))

    # ---- activity ----
    def pulse(self, seconds: float = 1.0):
        """Brighten/energise the orb for a moment - call this when Golem is thinking or speaking."""
        self._active_until = time.monotonic() + seconds

    def _check_signal(self):
        """Read the agent's activity signal and convert it to our monotonic clock.

        Line 1 is an epoch deadline (Golem is active until then). An optional second line,
        'SPEAK <start_epoch> <interval> <amp0> <amp1> ...', carries the loudness envelope of the voice
        now playing, so the orb can pulse with light exactly in time with the speech - no mouth, just the
        core flaring brighter on each syllable."""
        try:
            text = self._signal.read_text().strip()
        except Exception:
            return
        if not text:
            return
        lines = text.splitlines()
        mono_now, wall_now = time.monotonic(), time.time()
        try:
            remaining = float(lines[0].strip()) - wall_now
        except Exception:
            remaining = 0.0
        if remaining > 0:
            self._active_until = max(self._active_until, mono_now + min(remaining, 3.0))
        for ln in lines[1:]:
            parts = ln.split()
            if ln.startswith("LISTEN "):           # Golem is hearing you - show a distinct cool ring
                try:
                    rem = float(parts[1]) - wall_now
                except (IndexError, ValueError):
                    continue
                if rem > 0:
                    self._listen_until = max(self._listen_until, mono_now + min(rem, 2.0))
                continue
            if not ln.startswith("SPEAK "):
                continue
            try:
                start_epoch = float(parts[1])
                interval = float(parts[2])
                amps = [float(x) for x in parts[3:]]
            except (IndexError, ValueError):
                continue
            if not amps or interval <= 0:
                continue
            start_mono = mono_now + (start_epoch - wall_now)   # same instant, our clock
            end_mono = start_mono + len(amps) * interval
            if end_mono <= mono_now:
                continue                                        # already finished - ignore stale envelope
            self._speak_env = amps
            self._speak_start = start_mono
            self._speak_interval = interval
            self._speak_until = end_mono

    def _speak_amp(self, now: float) -> float:
        """Loudness (0..1) of the voice at this instant, or 0 when nothing is playing."""
        if not self._speak_env or now >= self._speak_until or now < self._speak_start:
            return 0.0
        i = int((now - self._speak_start) / self._speak_interval)
        if 0 <= i < len(self._speak_env):
            return max(0.0, min(1.0, self._speak_env[i]))
        return 0.0

    # ---- appearance ----
    def apply_orb(self, ap: dict):
        self.ap = ap
        if len(self.digits) != int(ap["density"]):
            self.digits = [Digit() for _ in range(int(ap["density"]))]
        self._glow = None          # force the glow pixmap to rebuild with new colours/size
        self._fonts.clear()
        self._resize_to_orb()
        self._place()
        self.setWindowOpacity(max(0.4, min(1.0, ap["opacity"] / 100.0)))
        self.update()

    def open_appearance(self):
        was = self.locked
        self.set_locked(False)     # need to receive clicks for the dialog to be usable
        OrbAppearanceDialog(self).exec()
        self.set_locked(was)

    def _font(self, px: int) -> QFont:
        f = self._fonts.get(px)
        if f is None:
            f = QFont("Consolas", px)
            f.setBold(True)
            self._fonts[px] = f
        return f

    def _glow_pixmap(self) -> QPixmap:
        key = (self.ap["core"], self.ap["glow"], self.ap["size"])
        if self._glow is not None and self._glow_key == key:
            return self._glow
        R = int(self.ap["size"])
        d = R * 4
        pm = QPixmap(d, d)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        c, g = QColor(self.ap["core"]), QColor(self.ap["glow"])
        grad = QRadialGradient(d / 2, d / 2, d / 2)
        hot = QColor(c); hot.setAlpha(255)
        mid = QColor(g); mid.setAlpha(230)
        mid2 = QColor(g); mid2.setAlpha(90)
        edge = QColor(g); edge.setAlpha(0)
        grad.setColorAt(0.0, hot)
        grad.setColorAt(0.16, QColor(c.red(), c.green(), c.blue(), 210))
        grad.setColorAt(0.32, mid)
        grad.setColorAt(0.60, mid2)
        grad.setColorAt(1.0, edge)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(0, 0, d, d)
        p.end()
        self._glow, self._glow_key = pm, key
        return pm

    # ---- the one hot loop ----
    def paintEvent(self, _e):
        now = time.monotonic()
        t = now - self._t0
        cx, cy = self.width() / 2, self.height() / 2
        cx += math.sin(t * 0.7) * 4.0        # gentle idle float, so it feels alive instead of pinned
        cy += math.sin(t * 0.9 + 1.3) * 5.0
        R = self.ap["size"] * 1.7
        amp = self._speak_amp(now)          # 0..1 loudness of the voice right now (pulses of light)
        speaking = now < self._speak_until
        listening = now < self._listen_until and not speaking   # hearing you (speaking wins if both)
        active = now < self._active_until or speaking or listening
        thinking = active and not speaking and not listening     # working on a reply - shimmer the data
        self.timer.setInterval(16 if active else 40)   # 60fps when alive, 25fps idle - easy on weak PCs

        # One mood dial: everything below reads from `energy`, eased toward a per-state target so the orb
        # glides between calm, curious, listening and talking as a single creature (no hard switches).
        target = 0.85 if speaking else (0.6 if listening else (0.5 if thinking else 0.12))
        dt = min(0.1, now - getattr(self, "_last_frame", now))
        self._last_frame = now
        self.energy += (target - self.energy) * min(1.0, dt * 3.0)   # ~0.3s glide
        e = self.energy
        pulse = e + 0.28 * amp              # instantaneous swell on each loud syllable rides on top

        # breathing + spin + size all scale off the one dial, so motion feels of-a-piece
        breathe = 1.0 + (0.045 + 0.085 * e) * math.sin(t * (1.6 + 1.3 * e)) + 0.26 * amp
        spin = t * (self.ap["speed"] / 100.0) * (0.8 + 1.7 * e)
        tilt = 0.42
        Reff = R * breathe

        digit_c = QColor(self.ap["digit"])
        spark_c = QColor(self.ap["spark"])

        # occasionally flip some digits so the data looks alive
        if now - self._last_flip > 0.05:
            self._last_flip = now
            for d in random.sample(self.digits, max(1, len(self.digits) // 40)):
                d.char = "1" if d.char == "0" else "0"

        # project every digit to screen with depth
        ct, st = math.cos(tilt), math.sin(tilt)
        items = []
        for d in self.digits:
            radial = math.sqrt(max(0.0, 1.0 - d.u * d.u))   # distance from the vertical axis
            ang = d.theta + spin
            rr = Reff * d.rr
            x = rr * radial * math.cos(ang)
            z = rr * radial * math.sin(ang)
            y = rr * d.u
            sx = cx + x
            sy = cy + y * ct - z * st
            depth = z * ct + y * st                          # +front .. -back
            items.append((depth, sx, sy, d))
        items.sort(key=lambda it: it[0])   # back-to-front

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        def draw_digits(front: bool):
            for depth, sx, sy, d in items:
                if (depth >= 0) != front:
                    continue
                dn = (depth + Reff) / (2 * Reff)              # 0 (far) .. 1 (near)
                tw = 0.5 + 0.5 * math.sin(t * 1.9 + d.phase)  # twinkle
                alpha = int((50 + 180 * dn) * (0.55 + 0.45 * tw))
                alpha = int(alpha * (0.85 + 0.95 * pulse))    # brightness rides the one mood dial + voice
                if thinking:   # a concentration shimmer sweeping the sphere while Golem works
                    alpha = int(alpha * (0.7 + 0.5 * (0.5 + 0.5 * math.sin(t * 3.0 + d.theta * 3.0))))
                px = max(8, int(9 + 11 * dn))
                col = QColor(spark_c if d.warm else digit_c)
                col.setAlpha(max(0, min(255, alpha)))
                p.setFont(self._font(px))
                p.setPen(QPen(col))
                p.drawText(QPointF(sx - px * 0.3, sy + px * 0.35), d.char)

        draw_digits(front=False)          # digits behind the core
        gp = self._glow_pixmap()          # the glowing core, pulsing
        gs = self.ap["size"] * 4 * breathe
        p.drawPixmap(QPointF(cx - gs / 2, cy - gs / 2),
                     gp.scaled(int(gs), int(gs), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if amp > 0.05:                    # a flare of light on each loud syllable - the orb "speaking"
            bloom = gs * (1.0 + 0.20 * amp)   # brighten more than grow, so it never reaches the window edge
            p.setOpacity(0.45 * amp)
            p.drawPixmap(QPointF(cx - bloom / 2, cy - bloom / 2),
                         gp.scaled(int(bloom), int(bloom), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            p.setOpacity(1.0)
        if listening:                     # a distinct cool ring while Golem is hearing you (not a mouth)
            ring = self.ap["size"] * 2.35
            puls = 0.5 + 0.5 * math.sin(t * 4.2)
            pen = QPen(QColor(150, 220, 255, int(60 + 150 * puls)))
            pen.setWidthF(3.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), ring, ring)
        draw_digits(front=True)           # digits in front of the core
        if self.face:                     # the avatar: eyes of light whose expression follows the mood
            self._draw_face(p, cx, cy, now, amp, speaking, listening, thinking)
        p.end()

    def _draw_face(self, p, cx, cy, now, amp, speaking, listening, thinking):
        sz = self.ap["size"]
        ex, ey = sz * 0.42, sz * 0.22     # eye offset from centre
        er = sz * 0.2                     # eye radius

        # blink: a quick dip every few seconds (randomised so it feels natural)
        if now >= self._next_blink:
            self._blink_start = now
            self._next_blink = now + random.uniform(3.0, 6.5)
        bt = now - self._blink_start
        blink = max(0.08, abs(bt - 0.08) / 0.08) if bt < 0.16 else 1.0

        # expression follows the mood: wide + alert listening, lively + "smiley" speaking,
        # half-lidded thinking, relaxed idle
        if speaking:
            openness, smile = 0.72 + 0.25 * amp, 0.5
        elif listening:
            openness, smile = 1.0, 0.0
        elif thinking:
            openness, smile = 0.7, 0.15
        else:
            openness, smile = 0.82, 0.1
        open_h = max(0.08, openness * blink)

        # look toward the cursor while listening; otherwise a slow idle wander
        if listening:
            try:
                g = QCursor.pos()
                dx, dy = (g.x() - self.x()) - cx, (g.y() - self.y()) - cy
                d = max(1.0, math.hypot(dx, dy))
                self._look_target = [dx / d * er * 0.5, dy / d * er * 0.5]
            except Exception:   # noqa: BLE001
                pass
        elif now >= self._next_look:
            self._next_look = now + random.uniform(1.6, 4.0)
            self._look_target = [random.uniform(-1, 1) * er * 0.35, random.uniform(-0.6, 0.6) * er * 0.35]
        self._look[0] += (self._look_target[0] - self._look[0]) * 0.12   # ease the glance
        self._look[1] += (self._look_target[1] - self._look[1]) * 0.12
        lookx, looky = self._look

        core = QColor(self.ap["core"])
        pupil = QColor(self.ap["glow"])
        for side in (-1, 1):
            cxr, cyr = cx + side * ex, cy - ey
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 12, 20, 180))      # dark socket so the eye reads against the glow
            p.drawEllipse(QPointF(cxr, cyr - smile * er * 0.12), er, er * open_h * (1.0 - 0.3 * smile))
            if open_h > 0.3:                         # pupil only while the eye is open
                p.setBrush(QColor(core.red(), core.green(), core.blue(), 235))
                p.drawEllipse(QPointF(cxr + lookx, cyr + looky), er * 0.52, er * 0.52)
                p.setBrush(QColor(pupil.red(), pupil.green(), pupil.blue(), 255))
                p.drawEllipse(QPointF(cxr + lookx, cyr + looky), er * 0.24, er * 0.24)
        p.setBrush(Qt.NoBrush)


class OrbAppearanceDialog(QDialog):
    COLORS = [("core", "Hot core"), ("glow", "Glow"), ("digit", "Digits"), ("spark", "Spark")]
    SLIDERS = [("size", "Size", 40, 130), ("density", "Data density", 30, 260),
               ("speed", "Speed", 5, 160), ("opacity", "Opacity", 40, 100)]

    def __init__(self, orb: Orb):
        super().__init__(orb)
        self.orb = orb
        self.setWindowTitle("Orb appearance")
        self.resize(360, 460)
        self._orig = dict(orb.ap)
        self.ap = dict(orb.ap)
        self._saved = False
        self._swatches = {}
        self._slabels = {}

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Theme:"))
        prow = QHBoxLayout()
        for name in ORB_THEMES:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self._theme(n))
            prow.addWidget(b)
        lay.addLayout(prow)

        grid = QGridLayout()
        for i, (key, label) in enumerate(self.COLORS):
            grid.addWidget(QLabel(label), i, 0)
            sw = QPushButton()
            sw.setFixedSize(60, 22)
            sw.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._swatches[key] = sw
            grid.addWidget(sw, i, 1)
        lay.addLayout(grid)

        self._sliders = {}
        for key, label, lo, hi in self.SLIDERS:
            row = QHBoxLayout()
            lbl = QLabel(f"{label}: {self.ap[key]}")
            self._slabels[key] = lbl
            row.addWidget(lbl)
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(int(self.ap[key]))
            s.valueChanged.connect(lambda v, k=key: self._slide(k, v))
            self._sliders[key] = s
            row.addWidget(s, 1)
            lay.addLayout(row)

        lay.addStretch(1)
        brow = QHBoxLayout()
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        brow.addWidget(reset)
        brow.addStretch(1)
        brow.addWidget(cancel)
        brow.addWidget(save)
        lay.addLayout(brow)
        self._refresh_swatches()

    def _refresh_swatches(self):
        for k, sw in self._swatches.items():
            sw.setStyleSheet(f"background:{self.ap[k]}; border:1px solid #555; border-radius:4px;")

    def _live(self):
        self.orb.apply_orb(dict(self.ap))

    def _theme(self, name):
        self.ap["theme"] = name
        self.ap.update(ORB_THEMES[name])
        self._refresh_swatches()
        self._live()

    def _pick(self, key):
        c = QColorDialog.getColor(QColor(self.ap[key]), self, "Pick a colour")
        if c.isValid():
            self.ap[key] = c.name()
            self._refresh_swatches()
            self._live()

    def _slide(self, key, v):
        self.ap[key] = int(v)
        self._slabels[key].setText(f"{dict((k, l) for k, l, _, _ in self.SLIDERS)[key]}: {v}")
        self._live()

    def _reset(self):
        self.ap = dict(DEFAULT_ORB)
        self.ap["x"], self.ap["y"] = self.orb.ap["x"], self.orb.ap["y"]
        for key, s in self._sliders.items():
            s.setValue(int(self.ap[key]))
        self._refresh_swatches()
        self._live()

    def _save(self):
        save_orb(self.ap)
        self._saved = True
        self.accept()

    def _cancel(self):
        self.orb.apply_orb(self._orig)
        self.reject()

    def closeEvent(self, e):
        if not self._saved:
            self.orb.apply_orb(self._orig)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    orb = Orb()
    orb.setWindowOpacity(max(0.4, min(1.0, orb.ap["opacity"] / 100.0)))
    orb.show()

    tray = QSystemTrayIcon(QIcon(str(ICON)) if ICON.exists() else QIcon(), app)
    tray.setToolTip("Golem Orb")
    menu = QMenu()
    a_talk = QAction("Talk to Golem")
    a_talk.triggered.connect(orb._request_talk)
    a_follow = QAction("Stop following windows" if orb.follow else "Follow my windows")
    a_follow.triggered.connect(lambda: (orb._toggle_follow(),
                                        a_follow.setText("Stop following windows" if orb.follow else "Follow my windows")))
    a_face = QAction("Hide face" if orb.face else "Show face")
    a_face.triggered.connect(lambda: (orb._toggle_face(),
                                      a_face.setText("Hide face" if orb.face else "Show face")))
    a_appr = QAction("Appearance...")
    a_appr.triggered.connect(orb.open_appearance)
    a_lock = QAction("Unlock to move")
    a_lock.triggered.connect(lambda: (orb.set_locked(not orb.locked),
                                      a_lock.setText("Lock in place" if not orb.locked else "Unlock to move")))
    a_hide = QAction("Hide / Show")
    a_hide.triggered.connect(lambda: orb.setVisible(not orb.isVisible()))
    a_quit = QAction("Quit")
    a_quit.triggered.connect(app.quit)
    menu.addAction(a_talk)
    menu.addAction(a_follow)
    menu.addAction(a_face)
    menu.addSeparator()
    for a in (a_appr, a_lock, a_hide):
        menu.addAction(a)
    menu.addSeparator()
    menu.addAction(a_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: orb.open_appearance() if r == QSystemTrayIcon.DoubleClick else None)
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
