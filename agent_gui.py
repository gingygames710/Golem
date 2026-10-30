"""Golem desktop - your agent in its own window.

A friendly chat window for the agent, so you never touch a terminal. It runs your configured
agent (from agent_config.json) and shows the conversation as chat bubbles; type in the box to
talk to it, and when it wants to do something on your PC, answer with the Allow / Deny buttons.

Runs either way with no change:
  - development: launches  python agent.py --config agent_config.json --chat
  - installed:  launches  golem-core.exe --config agent_config.json --chat   (no Python needed)
"""

import codecs
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame, QFileDialog,
    QColorDialog, QFontComboBox, QSlider, QDialog, QGridLayout, QSpinBox, QComboBox,
)

# Same control-line protocol the core (agent.py) parses to attach an image/file to the next message.
ATTACH_PREFIX = "\x1dGOLEM_ATTACH\x1d"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# When frozen into an .exe, __file__ is a temp extract dir; use the exe's own folder instead.
if getattr(sys, "frozen", False):
    AGENT_DIR = Path(sys.executable).resolve().parent
else:
    AGENT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = AGENT_DIR / "agent_config.json"
AGENT_PY = AGENT_DIR / "agent.py"
CORE_EXE = AGENT_DIR / "golem-core.exe"  # the frozen agent, present in an installed copy
ICON = AGENT_DIR / "icon.ico"

ANSI = re.compile(r"\x1b\[[0-9;]*m")            # colour codes to strip from the console output
PERMISSION_HINT = re.compile(r"\[y\]es", re.I)   # the agent's permission prompt marker
TOOLCALL = re.compile(r"^\s*->\s+(\S+)\s*(.*)$")  # the core's per-tool-call line ("  -> run_shell ...")
CREATE_NO_WINDOW = 0x08000000                    # keep the child process from flashing a console

# Palette - a warm, modern dark theme (not a black terminal).
BG      = "#0f1115"
PANEL   = "#161922"
USER_BG = "#4f46e5"   # indigo - your messages
AGENT_BG = "#232834"  # slate - the agent's messages
TEXT    = "#e7e9f0"
MUTED   = "#8b93a7"
ACCENT  = "#6366f1"

# 0.8.0 appearance: the palette above is the default ("Midnight"). The user can recolour the window,
# change the font, and set a background image from the agent window's Appearance panel; their choices
# are saved in the config under "appearance". Everything here is additive - no change if it's untouched.
DEFAULT_APPEARANCE = {
    "theme": "Midnight",
    "bg": BG, "panel": PANEL, "text": TEXT, "muted": MUTED, "accent": ACCENT,
    "user_bubble": USER_BG, "agent_bubble": AGENT_BG,
    "font_family": "Segoe UI", "font_size": 14,
    "bg_image": "", "bg_dim": 45,   # dim: 0-80, how much black to lay over the image so text stays readable
}
THEMES = {
    "Midnight":  {"bg": "#0f1115", "panel": "#161922", "text": "#e7e9f0", "muted": "#8b93a7",
                  "accent": "#6366f1", "user_bubble": "#4f46e5", "agent_bubble": "#232834"},
    "Light":     {"bg": "#f4f5f8", "panel": "#e6e8ef", "text": "#1a1d24", "muted": "#5b6475",
                  "accent": "#4f46e5", "user_bubble": "#4f46e5", "agent_bubble": "#dde1ea"},
    "Terminal":  {"bg": "#0a0e0a", "panel": "#0f160f", "text": "#c8f7c8", "muted": "#6f9a6f",
                  "accent": "#2ecc71", "user_bubble": "#1f7a3f", "agent_bubble": "#12240f"},
    "Solarized": {"bg": "#002b36", "panel": "#073642", "text": "#eee8d5", "muted": "#93a1a1",
                  "accent": "#268bd2", "user_bubble": "#268bd2", "agent_bubble": "#0b3d49"},
    "Rose":      {"bg": "#1a1016", "panel": "#241520", "text": "#f6e7ee", "muted": "#b08699",
                  "accent": "#e85d8a", "user_bubble": "#c13f6c", "agent_bubble": "#2c1a25"},
}


def load_appearance() -> dict:
    ap = dict(DEFAULT_APPEARANCE)
    try:
        saved = _config().get("appearance") or {}
        ap.update({k: v for k, v in saved.items() if k in DEFAULT_APPEARANCE and v is not None})
    except Exception:
        pass
    return ap


def save_appearance(ap: dict):
    try:
        cfg = _config()
        cfg["appearance"] = ap
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def _apply_palette_globals(ap: dict):
    """Point the module palette constants at the user's chosen colours, so the whole window builds themed
    without touching every stylesheet. Only one window exists, so reusing the globals is safe."""
    global BG, PANEL, TEXT, MUTED, ACCENT, USER_BG, AGENT_BG
    BG, PANEL, TEXT, MUTED, ACCENT = ap["bg"], ap["panel"], ap["text"], ap["muted"], ap["accent"]
    USER_BG, AGENT_BG = ap["user_bubble"], ap["agent_bubble"]


def agent_python() -> str:
    venv = AGENT_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin") / \
        ("python.exe" if os.name == "nt" else "python")
    return str(venv) if venv.exists() else sys.executable


def agent_command() -> list:
    """How to start the agent in chat mode - the frozen core if installed, else the script."""
    if CORE_EXE.exists():
        return [str(CORE_EXE), "--config", str(CONFIG_FILE), "--chat"]
    return [agent_python(), "-u", str(AGENT_PY), "--config", str(CONFIG_FILE), "--chat"]


def agent_name() -> str:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("name") or "Golem"
    except Exception:
        return "Golem"


def _config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def agent_hardened() -> bool:
    """True when the saved config has Hardened Mode on, so the window can show a lock badge."""
    return bool(_config().get("hardened"))


def agent_sandboxed() -> bool:
    """True when the saved config turns on the OS sandbox, for a stronger badge."""
    return bool((_config().get("sandbox") or {}).get("enabled"))


def agent_profile_label() -> str:
    """The chosen profile's label (empty for the plain General profile), for a header chip."""
    pid = _config().get("profile", "general")
    if not pid or pid == "general":
        return ""
    try:
        import agent
        return agent.PROFILES.get(pid, {}).get("label", "")
    except Exception:
        return pid.title()


class Reader(QThread):
    """Reads the agent's output in chunks (not lines, so a no-newline prompt still shows)."""
    chunk = Signal(str)
    ended = Signal()

    def __init__(self, proc):
        super().__init__()
        self.proc = proc

    def run(self):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        fd = self.proc.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = dec.decode(data)
            if text:
                self.chunk.emit(text)
        self.ended.emit()


# ---------------------------------------------------------------- voice (0.9.0)
#
# The agent can read its replies aloud with the same local Piper TTS the agent already uses
# (generate_speech), run from the gen/ environment. Pretrained voices only for now - the user picks a
# voice (and Piper downloads it on first use); clone-from-your-own-sample is a planned heavier add-on.
GEN_DIR = AGENT_DIR / "gen"
VOICES_DIR = GEN_DIR / "voices"
DEFAULT_VOICE = "en_US-lessac-medium"


def gen_python() -> str:
    p = GEN_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    return str(p) if p.exists() else ""


def piper_available() -> bool:
    return bool(gen_python())


_VOICE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_voice(voice: str) -> str:
    """Voice names are used in a path and passed to Piper; keep them to a safe charset so a crafted
    name can't escape the voices folder. Fall back to the default if it looks wrong."""
    v = (voice or "").strip()
    return v if _VOICE_RE.match(v) else DEFAULT_VOICE


def ensure_voice(voice: str) -> bool:
    """Make sure the Piper voice model is downloaded (Piper 1.3+ no longer fetches it during synth).
    Returns True when the voice is present and usable."""
    voice = _safe_voice(voice)
    gp = gen_python()
    if not gp:
        return False
    if (VOICES_DIR / f"{voice}.onnx").exists():
        return True
    try:
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run([gp, "-m", "piper.download_voices", voice, "--download-dir", str(VOICES_DIR)],
                       capture_output=True, text=True, timeout=600, cwd=str(GEN_DIR),
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    return (VOICES_DIR / f"{voice}.onnx").exists()


_MD = re.compile(r"[`*#_>~\[\]]|\(https?://[^)]+\)")


def _clean_for_speech(text: str) -> str:
    t = re.sub(r"```.*?```", " (code) ", text, flags=re.S)   # don't read code blocks aloud
    t = _MD.sub("", t).strip()
    if len(t) > 700:                                         # trim long replies at a natural boundary
        cut = t[:700]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
        t = cut[:end + 1] if end > 400 else cut.rsplit(" ", 1)[0]
    return t


class VoicePlayer(QThread):
    """Synthesize text with Piper and play it, off the UI thread so the window never freezes."""
    done = Signal()

    def __init__(self, text: str, voice: str):
        super().__init__()
        self.text, self.voice = text, _safe_voice(voice)

    def run(self):
        wav = None
        try:
            gp = gen_python()
            if not gp or not self.text.strip():
                return
            ensure_voice(self.voice)   # download the voice model the first time it's used
            if not (VOICES_DIR / f"{self.voice}.onnx").exists():
                return
            wav = Path(tempfile.gettempdir()) / f"golem-say-{int(time.time() * 1000)}.wav"
            subprocess.run([gp, "-m", "piper", "-m", self.voice, "-f", str(wav), "--data-dir", str(VOICES_DIR)],
                           input=self.text, text=True, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=300, cwd=str(GEN_DIR),
                           creationflags=CREATE_NO_WINDOW)
            if wav.exists() and wav.stat().st_size > 100 and os.name == "nt":
                import winsound
                winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        except Exception:
            pass
        finally:
            if wav is not None:
                try:
                    wav.unlink(missing_ok=True)   # don't leave temp WAVs piling up
                except Exception:
                    pass
            self.done.emit()


class AgentWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.name = agent_name()
        self.setWindowTitle(f"{self.name} - Golem")
        self.resize(820, 720)
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.ap = load_appearance()
        _apply_palette_globals(self.ap)   # theme the whole window before any widgets are built
        self.bubbles = []                 # (label, who): so an appearance change can recolour past messages
        self._bg_path = ""
        self._refresh_bg_pixmap()
        self.setStyleSheet(self._window_qss())

        vc = _config().get("voice") or {}
        self.speak_on = bool(vc.get("enabled"))
        self.voice_name = vc.get("voice") or DEFAULT_VOICE
        self._voice_thread = None
        self._spoken_text = ""        # last text spoken aloud, so a reply isn't read twice
        self._warned_no_piper = False
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.timeout.connect(self._speak_current)

        self.proc = None
        self.reader = None
        self.cur_agent = None     # the QLabel of the agent's in-progress message bubble
        self.agent_text = ""      # text committed to that bubble
        self.line_buf = ""        # output not yet split into a full line
        self._user_stopped = False  # true when the user pressed Stop (so "stopped" reads intentionally)
        self.pending = []         # attachments staged for the next message: list of file paths

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._holder = holder = QWidget()
        self.col = QVBoxLayout(holder)
        self.col.setAlignment(Qt.AlignTop)
        self.col.setContentsMargins(18, 14, 18, 14)
        self.col.setSpacing(10)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _mn, mx: self.scroll.verticalScrollBar().setValue(mx))
        self._apply_bg_backdrop()   # paint the custom background image behind the chat, if one is set

        root.addWidget(self._permission_bar())
        root.addWidget(self._composer())

        self.start_agent()
        if _config().get("orb_autostart"):
            self._launch_orb()

    def _launch_orb(self):
        """Start the ambient orb (the frozen exe if installed, else orb.py). Best-effort."""
        try:
            exe = AGENT_DIR / "Golem-Orb.exe"
            if exe.exists():
                subprocess.Popen([str(exe)], cwd=str(AGENT_DIR))
            else:
                py = agent_python()
                if py.endswith("python.exe") and os.path.exists(py[:-10] + "pythonw.exe"):
                    py = py[:-10] + "pythonw.exe"
                subprocess.Popen([py, str(AGENT_DIR / "orb.py")], cwd=str(AGENT_DIR))
        except Exception:
            pass

    # ---------------------------------------------------------------- appearance (0.8.0)

    def _window_qss(self) -> str:
        ap = self.ap
        return (
            f"QWidget {{ background:{ap['bg']}; color:{ap['text']}; "
            f"font-family:'{ap['font_family']}','Segoe UI','Inter',sans-serif; font-size:{ap['font_size']}px; }}"
            " QScrollArea { border:none; background:transparent; }"
            " QScrollBar:vertical { background:transparent; width:10px; margin:2px; }"
            " QScrollBar::handle:vertical { background:#2c3342; border-radius:5px; min-height:30px; }"
            " QScrollBar::add-line, QScrollBar::sub-line { height:0; }")

    def _entry_qss(self) -> str:
        return (f"background:{self.ap['panel']}; color:{self.ap['text']}; padding:11px 14px; "
                f"border:1px solid {self.ap['muted']}; border-radius:12px; font-size:14px;")

    def _refresh_bg_pixmap(self):
        """Prepare the background image: load it, lay the chosen amount of black over it (so text stays
        readable), and save the result next to the agent for the stylesheet to use. No image -> nothing."""
        self._bg_path = ""
        src = (self.ap.get("bg_image") or "").strip()
        if not src or not Path(src).exists():
            return
        pm = QPixmap(src)
        if pm.isNull():
            return
        dim = max(0, min(80, int(self.ap.get("bg_dim", 45)))) / 100.0
        if dim > 0:
            p = QPainter(pm)
            p.fillRect(pm.rect(), QColor(0, 0, 0, int(dim * 255)))
            p.end()
        out = AGENT_DIR / ".golem_bg.png"
        try:
            if pm.save(str(out), "PNG"):
                self._bg_path = str(out)
        except Exception:
            self._bg_path = ""

    def _apply_bg_backdrop(self):
        vp = self.scroll.viewport()
        if self._bg_path:
            url = self._bg_path.replace("\\", "/")
            vp.setStyleSheet(f"border-image: url('{url}') 0 0 0 0 stretch stretch;")
            self._holder.setStyleSheet("background:transparent;")
        else:
            vp.setStyleSheet(f"background:{self.ap['bg']};")
            self._holder.setStyleSheet(f"background:{self.ap['bg']};")

    def apply_appearance(self, ap: dict):
        """Re-theme the live window from an appearance dict (used by the Appearance panel's preview)."""
        self.ap = ap
        _apply_palette_globals(ap)
        self.setStyleSheet(self._window_qss())
        self._header_bar.setStyleSheet(f"background:{PANEL};")
        self._name_lbl.setStyleSheet(f"color:{TEXT}; font-size:17px; font-weight:600;")
        self.sub.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        self.perm_bar.setStyleSheet(f"background:{PANEL}; border-top:1px solid #2a2f3a;")
        self._composer_bar.setStyleSheet(f"background:{PANEL}; border-top:1px solid #20242e;")
        self.entry.setStyleSheet(self._entry_qss())
        self.send_btn.setStyleSheet(f"QPushButton {{ background:{ACCENT}; color:white; padding:11px 20px; "
                                    f"border:none; border-radius:12px; font-weight:600; }}")
        for lbl, who in self.bubbles:
            if who == "user":
                lbl.setStyleSheet(f"background:{USER_BG}; color:white; padding:10px 14px; border-radius:14px;")
            elif who == "agent":
                lbl.setStyleSheet(f"background:{AGENT_BG}; color:{TEXT}; padding:10px 14px; border-radius:14px;")
            elif who == "system":
                lbl.setStyleSheet(f"color:{MUTED}; font-size:12px; padding:2px;")
        self._refresh_bg_pixmap()
        self._apply_bg_backdrop()

    def open_appearance(self):
        AppearanceDialog(self).exec()

    # ---------------------------------------------------------------- voice (0.9.0)

    def _toggle_speak(self, on: bool):
        self.speak_on = on
        if on and not piper_available() and not self._warned_no_piper:
            self._warned_no_piper = True
            self._bubble("To hear replies, install the image & sound pack in the Builder (Add-ons tab) - "
                         "it includes the Piper voice.", "system")
        self._save_voice_cfg()

    def _save_voice_cfg(self):
        try:
            cfg = _config()
            cfg["voice"] = {"enabled": self.speak_on, "voice": self.voice_name}
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _speak_current(self):
        """Fired a moment after the agent stops producing output: read the finished reply aloud once."""
        if not self.speak_on or not piper_available():
            return
        text = _clean_for_speech(self.agent_text or "")
        if not text or text == self._spoken_text:
            return
        if self._voice_thread and self._voice_thread.isRunning():
            self._speak_timer.start(800)   # busy speaking the previous reply; try again shortly
            return
        self._spoken_text = text
        self._ping_orb(2.5)                 # a stronger, longer pulse while it's actually speaking
        self._voice_thread = VoicePlayer(text, self.voice_name)
        self._voice_thread.start()

    def _maybe_queue_speak(self):
        self._ping_orb()                    # let the ambient orb (if running) pulse while Golem responds
        if self.speak_on:
            self._speak_timer.start(1100)   # debounce: speak once output settles for ~1.1s

    def _ping_orb(self, seconds: float = 1.3):
        """Tell the ambient orb Golem is active, via a tiny shared file. Throttled; best-effort."""
        now = time.time()
        if now - getattr(self, "_last_orb_ping", 0.0) < 0.4:
            return
        self._last_orb_ping = now
        try:
            (Path(tempfile.gettempdir()) / "golem_orb_signal").write_text(str(now + seconds))
        except Exception:
            pass

    # ---------------------------------------------------------------- building blocks

    def _header(self) -> QWidget:
        self._header_bar = bar = QFrame()
        bar.setStyleSheet(f"background:{PANEL};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 16, 10)
        avatar = QLabel()
        if ICON.exists():
            avatar.setPixmap(QPixmap(str(ICON)).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(avatar)
        box = QVBoxLayout()
        box.setSpacing(0)
        self._name_lbl = nm = QLabel(self.name)
        nm.setStyleSheet(f"color:{TEXT}; font-size:17px; font-weight:600;")
        self.sub = QLabel("starting up...")
        self.sub.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        box.addWidget(nm)
        box.addWidget(self.sub)
        h.addLayout(box)
        prof = agent_profile_label()
        if prof:
            chip = QLabel(prof)
            chip.setStyleSheet("color:#9ab; background:#1b2230; border:1px solid #2a3a4a; "
                               "border-radius:8px; padding:4px 10px; font-size:12px; font-weight:600;")
            chip.setToolTip(f"This agent is set up with the {prof} profile.")
            h.addSpacing(10)
            h.addWidget(chip)
        if agent_sandboxed():
            sb = QLabel("🧱 Sandboxed")
            sb.setStyleSheet("color:#8fd0d0; background:#162a2a; border:1px solid #2f4a4a; "
                             "border-radius:8px; padding:4px 10px; font-size:12px; font-weight:600;")
            sb.setToolTip("Sandbox Mode: the agent's shell commands run inside an OS box where only your "
                          "allowed folders exist, so a command can't reach the rest of your disk.")
            h.addSpacing(8)
            h.addWidget(sb)
        elif agent_hardened():
            badge = QLabel("🔒 Hardened")
            badge.setStyleSheet("color:#8fd08f; background:#1c2a1c; border:1px solid #2f4a2f; "
                                "border-radius:8px; padding:4px 10px; font-size:12px; font-weight:600;")
            badge.setToolTip("Hardened Mode is on: the agent is confined to your allowed folders, stays "
                             "extra-careful after reading untrusted content, and phone access is PC-only.")
            h.addSpacing(10)
            h.addWidget(badge)
        h.addStretch(1)
        self.speak_btn = QPushButton("🔊")
        self.speak_btn.setCheckable(True)
        self.speak_btn.setChecked(self.speak_on)
        self.speak_btn.setCursor(Qt.PointingHandCursor)
        self.speak_btn.setToolTip("Read the agent's replies out loud")
        self.speak_btn.setStyleSheet(
            "QPushButton { background:transparent; border:1px solid #2a2f3a; border-radius:8px; "
            "padding:6px 11px; font-size:15px; }"
            " QPushButton:checked { background:#1f6feb33; border-color:#1f6feb; }")
        self.speak_btn.toggled.connect(self._toggle_speak)
        h.addWidget(self.speak_btn)
        self.appear_btn = QPushButton("🎨")
        self.appear_btn.setCursor(Qt.PointingHandCursor)
        self.appear_btn.setToolTip("Appearance - colors, font, and background image")
        self.appear_btn.setStyleSheet("background:transparent; border:1px solid #2a2f3a; "
                                      "border-radius:8px; padding:6px 11px; font-size:15px;")
        self.appear_btn.clicked.connect(self.open_appearance)
        h.addWidget(self.appear_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setStyleSheet("color:#f0c674; background:transparent; border:1px solid #4a3f2a; "
                                    "border-radius:8px; padding:6px 12px;")
        self.stop_btn.clicked.connect(self.stop)
        h.addWidget(self.stop_btn)
        self.restart_btn = QPushButton("Restart")
        self.restart_btn.setCursor(Qt.PointingHandCursor)
        self.restart_btn.setStyleSheet(
            f"color:{MUTED}; background:transparent; border:1px solid #2a2f3a; "
            "border-radius:8px; padding:6px 12px;")
        self.restart_btn.clicked.connect(self.restart)
        h.addWidget(self.restart_btn)
        return bar

    def _permission_bar(self) -> QWidget:
        self.perm_bar = QFrame()
        self.perm_bar.setStyleSheet(f"background:{PANEL}; border-top:1px solid #2a2f3a;")
        pb = QHBoxLayout(self.perm_bar)
        pb.setContentsMargins(16, 10, 16, 10)
        self.perm_label = QLabel("The agent wants to do something on your PC.")
        self.perm_label.setStyleSheet("color:#f0c674; font-size:13px;")
        pb.addWidget(self.perm_label, 1)
        for text, letter, color in [("Allow", "y", "#2e7d32"), ("Always", "a", "#2d6cdf"), ("Deny", "n", "#b03b3b")]:
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"background:{color}; color:white; padding:7px 16px; border:none; border-radius:8px;")
            b.clicked.connect(lambda _=False, l=letter: self.answer(l))
            pb.addWidget(b)
        self.perm_bar.setVisible(False)
        return self.perm_bar

    def _composer(self) -> QWidget:
        self._composer_bar = bar = QFrame()
        bar.setStyleSheet(f"background:{PANEL}; border-top:1px solid #20242e;")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 10, 16, 14)
        outer.setSpacing(8)

        # A row of chips for files/images staged for the next message (hidden until something is attached).
        self.attach_row = QFrame()
        self.attach_flow = QHBoxLayout(self.attach_row)
        self.attach_flow.setContentsMargins(0, 0, 0, 0)
        self.attach_flow.setSpacing(6)
        self.attach_flow.setAlignment(Qt.AlignLeft)
        self.attach_row.setVisible(False)
        outer.addWidget(self.attach_row)

        h = QHBoxLayout()
        h.setSpacing(8)
        self.attach_btn = self._icon_button("📎", "Attach a file or image")
        self.attach_btn.clicked.connect(self.attach_file)
        h.addWidget(self.attach_btn)
        self.shot_btn = self._icon_button("🖼", "Send a screenshot")
        self.shot_btn.clicked.connect(self.attach_screenshot)
        h.addWidget(self.shot_btn)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText(f"Message {self.name}...")
        self.entry.setStyleSheet(self._entry_qss())
        self.entry.returnPressed.connect(self.send)
        h.addWidget(self.entry, 1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; padding:11px 20px; border:none; "
            "border-radius:12px; font-weight:600; }} QPushButton:hover { background:#7c7ff5; }")
        self.send_btn.clicked.connect(self.send)
        h.addWidget(self.send_btn)
        outer.addLayout(h)
        return bar

    def _icon_button(self, glyph: str, tip: str) -> QPushButton:
        b = QPushButton(glyph)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setFixedSize(42, 42)
        b.setStyleSheet(
            "QPushButton { background:#1b1f27; color:#c7ccda; border:1px solid #2a2f3a; "
            "border-radius:12px; font-size:18px; } QPushButton:hover { background:#232834; }")
        return b

    def _bubble(self, text: str, who: str) -> QLabel:
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setMaximumWidth(560)
        if who == "user":
            lbl.setStyleSheet(f"background:{USER_BG}; color:white; padding:10px 14px; border-radius:14px;")
            hl.addStretch(1)
            hl.addWidget(lbl)
        elif who == "agent":
            lbl.setStyleSheet(f"background:{AGENT_BG}; color:{TEXT}; padding:10px 14px; border-radius:14px;")
            hl.addWidget(lbl)
            hl.addStretch(1)
        elif who == "tool":  # a tool-call chip
            lbl.setMaximumWidth(560)
            lbl.setStyleSheet("background:#171f2e; color:#8fb0e0; padding:6px 12px; border-radius:10px; "
                              "border:1px solid #2b3a55; font-family:Consolas,monospace; font-size:12px;")
            hl.addWidget(lbl)
            hl.addStretch(1)
        else:  # system notice
            lbl.setMaximumWidth(680)
            lbl.setStyleSheet(f"color:{MUTED}; font-size:12px; padding:2px;")
            lbl.setAlignment(Qt.AlignCenter)
            hl.addStretch(1)
            hl.addWidget(lbl)
            hl.addStretch(1)
        if who in ("user", "agent", "system"):
            self.bubbles.append((lbl, who))   # so the Appearance panel can recolour it live
        self.col.addWidget(row)
        return lbl

    # ---------------------------------------------------------------- process

    def _running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start_agent(self):
        if not CONFIG_FILE.exists():
            self._bubble("No agent is set up yet. Open the Builder and click Save first.", "system")
            self.sub.setText("not set up")
            return
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                agent_command(), cwd=str(AGENT_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, env=env, creationflags=flags,
            )
        except Exception as e:  # noqa: BLE001
            self._bubble(f"Could not start the agent: {e}", "system")
            return
        self.reader = Reader(self.proc)
        self.reader.chunk.connect(self.on_chunk)
        self.reader.ended.connect(self.on_ended)
        self.reader.start()
        self.sub.setText("online")

    def restart(self):
        self._user_stopped = False
        self._terminate()
        while self.col.count():
            item = self.col.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cur_agent, self.agent_text, self.line_buf = None, "", ""
        self.pending.clear()
        self._render_attachments()
        self.perm_bar.setVisible(False)
        self.start_agent()

    def _terminate(self):
        if self._running():
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        if self.reader:
            self.reader.wait(2000)

    def on_ended(self):
        if not self._user_stopped:
            self._bubble("The agent stopped. Click Restart to run it again.", "system")
            self.sub.setText("stopped")

    def stop(self):
        """Halt whatever the agent is doing right now (keeps the transcript)."""
        if not self._running():
            return
        self._user_stopped = True
        self._terminate()
        self._bubble("Stopped. Click Restart to chat again.", "system")
        self.sub.setText("stopped")

    # ---------------------------------------------------------------- streaming -> bubbles

    def on_chunk(self, raw: str):
        raw = ANSI.sub("", raw)
        if PERMISSION_HINT.search(raw):
            self.perm_bar.setVisible(True)
        data = self.line_buf + raw
        parts = data.split("\n")
        self.line_buf = parts[-1]
        for line in parts[:-1]:
            self._commit_line(line)
        tail = self.line_buf
        if tail and not tail.lstrip().startswith(("[", "?")) and "[y]es" not in tail:
            self._preview_agent(tail)

    def _commit_line(self, line: str):
        s = line.strip()
        if "[y]es" in line:          # the permission prompt itself - the bar handles it
            return
        if not s:
            return
        m = TOOLCALL.match(line)
        if m:                        # a tool call -> a tidy chip, not raw text
            name, detail = m.group(1), m.group(2).strip()
            if len(detail) > 60:
                detail = detail[:57] + "..."
            self.cur_agent = None
            self._bubble(f"⚙  {name}" + (f"  {detail}" if detail else ""), "tool")
            return
        if s.startswith("[") or s.startswith("?"):   # a status / permission notice
            self.cur_agent = None
            self._bubble(s.strip("[]").strip(), "system")
            return
        self._append_agent(line)     # ordinary agent speech

    def _append_agent(self, line: str):
        if self.cur_agent is None:
            self.cur_agent = self._bubble("", "agent")
            self.agent_text = ""
            self._spoken_text = ""        # a fresh reply may be read aloud
        self.agent_text += (("\n" if self.agent_text else "") + line)
        self.cur_agent.setText(self.agent_text)
        self._maybe_queue_speak()

    def _preview_agent(self, tail: str):
        if self.cur_agent is None:
            self.cur_agent = self._bubble("", "agent")
            self.agent_text = ""
            self._spoken_text = ""
        self.cur_agent.setText(self.agent_text + (("\n" if self.agent_text else "") + tail))
        self._maybe_queue_speak()

    # ---------------------------------------------------------------- input

    # ---------------------------------------------------------------- attachments

    def attach_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach a file or image", "",
            "All files (*.*);;Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        if path:
            self._stage(path)

    def attach_screenshot(self):
        """Hide the window briefly, grab the whole screen, then stage it as an image."""
        if not self._running():
            self._bubble("The agent isn't running - click Restart.", "system")
            return
        self.showMinimized()
        QTimer.singleShot(350, self._grab_screen)

    def _grab_screen(self):
        try:
            screen = QApplication.primaryScreen()
            pix = screen.grabWindow(0)
            out = Path(tempfile.gettempdir()) / f"golem-shot-{int(time.time())}.png"
            pix.save(str(out), "PNG")
            self._stage(str(out))
        except Exception as e:  # noqa: BLE001
            self._bubble(f"Could not take a screenshot: {e}", "system")
        finally:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _stage(self, path: str):
        self.pending.append(path)
        self._render_attachments()

    def _render_attachments(self):
        while self.attach_flow.count():
            item = self.attach_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for path in self.pending:
            self.attach_flow.addWidget(self._attach_chip(path))
        self.attach_flow.addStretch(1)
        self.attach_row.setVisible(bool(self.pending))

    def _attach_chip(self, path: str) -> QWidget:
        chip = QFrame()
        chip.setStyleSheet("background:#171f2e; border:1px solid #2b3a55; border-radius:10px;")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(8, 4, 6, 4)
        cl.setSpacing(6)
        is_img = Path(path).suffix.lower() in IMAGE_SUFFIXES
        if is_img:
            thumb = QLabel()
            thumb.setPixmap(QPixmap(path).scaled(28, 28, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            cl.addWidget(thumb)
        name = QLabel(("🖼 " if is_img else "📄 ") + Path(path).name)
        name.setStyleSheet("color:#9fb4d4; font-size:12px; background:transparent; border:none;")
        cl.addWidget(name)
        x = QPushButton("✕")
        x.setCursor(Qt.PointingHandCursor)
        x.setFixedSize(18, 18)
        x.setStyleSheet("QPushButton { color:#8b93a7; background:transparent; border:none; font-size:12px; }"
                        " QPushButton:hover { color:#e7e9f0; }")
        x.clicked.connect(lambda _=False, p=path: self._unstage(p))
        cl.addWidget(x)
        return chip

    def _unstage(self, path: str):
        if path in self.pending:
            self.pending.remove(path)
        self._render_attachments()

    # ---------------------------------------------------------------- send

    def send(self):
        text = self.entry.text().strip()
        if not text and not self.pending:
            return
        if not self._running():
            self._bubble("The agent isn't running - click Restart.", "system")
            return
        self.entry.clear()
        self.perm_bar.setVisible(False)
        self.cur_agent, self.agent_text = None, ""   # next agent output starts a fresh bubble
        attachments = list(self.pending)
        self.pending.clear()
        self._render_attachments()
        if attachments:
            if text:
                self._bubble(text, "user")
            for path in attachments:
                self._attachment_bubble(path)
            payload = {"message": text, "images": [], "files": []}
            for path in attachments:
                key = "images" if Path(path).suffix.lower() in IMAGE_SUFFIXES else "files"
                payload[key].append(path)
            self._write(ATTACH_PREFIX + json.dumps(payload))
        else:
            self._bubble(text, "user")
            self._write(text)

    def _attachment_bubble(self, path: str):
        """Show what the user attached, as a right-aligned bubble (thumbnail for an image)."""
        is_img = Path(path).suffix.lower() in IMAGE_SUFFIXES
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        if is_img:
            pix = QPixmap(path)
            if not pix.isNull():
                lbl = QLabel()
                lbl.setPixmap(pix.scaledToWidth(min(260, pix.width()), Qt.SmoothTransformation))
                lbl.setStyleSheet("border-radius:12px;")
                hl.addWidget(lbl)
                self.col.addWidget(row)
                return
        lbl = QLabel(("🖼 " if is_img else "📄 ") + Path(path).name)
        lbl.setStyleSheet(f"background:{USER_BG}; color:white; padding:10px 14px; border-radius:14px;")
        hl.addWidget(lbl)
        self.col.addWidget(row)

    def answer(self, letter: str):
        self.perm_bar.setVisible(False)
        if self._running():
            self._write(letter)

    def _write(self, line: str):
        try:
            self.proc.stdin.write((line + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            self._bubble("Could not reach the agent - click Restart.", "system")

    def closeEvent(self, event):
        try:
            if os.name == "nt":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)   # stop any in-progress speech
        except Exception:
            pass
        if getattr(self, "_voice_thread", None) is not None:
            try:
                self._voice_thread.wait(1500)   # let the voice thread finish before we tear down
            except Exception:
                pass
        self._terminate()
        event.accept()


class AppearanceDialog(QDialog):
    """Make the agent window feel like yours: theme presets, colour pickers, font, and a background
    image. Every change previews live on the window; Save writes it to the config."""
    COLORS = [("bg", "Background"), ("panel", "Panels"), ("text", "Text"),
              ("user_bubble", "Your messages"), ("agent_bubble", "Agent messages"), ("accent", "Accent / Send")]

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Appearance")
        self.resize(430, 580)
        self._orig = dict(window.ap)   # restore this if the user cancels
        self.ap = dict(window.ap)      # working copy that previews live
        self._saved = False
        self._swatches = {}

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Start from a theme:"))
        prow = QHBoxLayout()
        for name in THEMES:
            b = QPushButton(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self._apply_theme(n))
            prow.addWidget(b)
        lay.addLayout(prow)

        lay.addWidget(QLabel("Colours (click a swatch):"))
        grid = QGridLayout()
        for i, (key, label) in enumerate(self.COLORS):
            grid.addWidget(QLabel(label), i, 0)
            sw = QPushButton()
            sw.setFixedSize(64, 24)
            sw.setCursor(Qt.PointingHandCursor)
            sw.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self._swatches[key] = sw
            grid.addWidget(sw, i, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Font:"))
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.ap["font_family"]))
        self.font_combo.currentFontChanged.connect(lambda f: self._set("font_family", f.family()))
        frow.addWidget(self.font_combo, 1)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(10, 26)
        self.size_spin.setValue(int(self.ap["font_size"]))
        self.size_spin.valueChanged.connect(lambda v: self._set("font_size", int(v)))
        frow.addWidget(self.size_spin)
        lay.addLayout(frow)

        lay.addWidget(QLabel("Background image (optional):"))
        brow = QHBoxLayout()
        self.bg_label = QLabel(self._bg_name())
        self.bg_label.setStyleSheet("color:#8b93a7;")
        choose = QPushButton("Choose...")
        choose.clicked.connect(self._pick_bg)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_bg)
        brow.addWidget(self.bg_label, 1)
        brow.addWidget(choose)
        brow.addWidget(clear)
        lay.addLayout(brow)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Darken image:"))
        self.dim_slider = QSlider(Qt.Horizontal)
        self.dim_slider.setRange(0, 80)
        self.dim_slider.setValue(int(self.ap["bg_dim"]))
        self.dim_slider.valueChanged.connect(lambda v: self._set("bg_dim", int(v)))
        drow.addWidget(self.dim_slider, 1)
        lay.addLayout(drow)

        lay.addStretch(1)
        brow2 = QHBoxLayout()
        reset = QPushButton("Reset to default")
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        save = QPushButton("Save")
        save.setStyleSheet(f"background:{ACCENT}; color:white; font-weight:600; padding:8px 18px; "
                           "border:none; border-radius:8px;")
        save.clicked.connect(self._save)
        brow2.addWidget(reset)
        brow2.addStretch(1)
        brow2.addWidget(cancel)
        brow2.addWidget(save)
        lay.addLayout(brow2)
        self._refresh_swatches()

    def _bg_name(self):
        p = self.ap.get("bg_image", "")
        return Path(p).name if p else "(none)"

    def _refresh_swatches(self):
        for key, sw in self._swatches.items():
            sw.setStyleSheet(f"background:{self.ap[key]}; border:1px solid #555; border-radius:4px;")

    def _live(self):
        self.window.apply_appearance(dict(self.ap))

    def _set(self, key, val):
        self.ap[key] = val
        self._live()

    def _apply_theme(self, name):
        self.ap["theme"] = name
        self.ap.update(THEMES[name])
        self._refresh_swatches()
        self._live()

    def _pick_color(self, key):
        c = QColorDialog.getColor(QColor(self.ap[key]), self, "Pick a colour")
        if c.isValid():
            self.ap[key] = c.name()
            self._refresh_swatches()
            self._live()

    def _pick_bg(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose a background image", "",
                                           "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not f:
            return
        try:
            import shutil
            dest = AGENT_DIR / ("bg_custom" + Path(f).suffix.lower())
            shutil.copy2(f, dest)   # keep a copy with the app so it persists
            self.ap["bg_image"] = str(dest)
        except Exception:
            self.ap["bg_image"] = f
        self.bg_label.setText(self._bg_name())
        self._live()

    def _clear_bg(self):
        self.ap["bg_image"] = ""
        self.bg_label.setText(self._bg_name())
        self._live()

    def _reset(self):
        self.ap = dict(DEFAULT_APPEARANCE)
        self.font_combo.setCurrentFont(QFont(self.ap["font_family"]))
        self.size_spin.setValue(int(self.ap["font_size"]))
        self.dim_slider.setValue(int(self.ap["bg_dim"]))
        self.bg_label.setText(self._bg_name())
        self._refresh_swatches()
        self._live()

    def _save(self):
        save_appearance(self.ap)
        self._saved = True
        self.accept()

    def _cancel(self):
        self.window.apply_appearance(self._orig)   # revert the live preview
        self.reject()

    def closeEvent(self, e):
        if not self._saved:
            self.window.apply_appearance(self._orig)   # revert on the window-close (X) too
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    win = AgentWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
