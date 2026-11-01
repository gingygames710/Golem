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
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QColor, QPainter, QAction
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame, QFileDialog,
    QColorDialog, QFontComboBox, QSlider, QDialog, QGridLayout, QSpinBox, QComboBox,
    QSystemTrayIcon, QMenu,
)

try:   # mic capture for talk-to-Golem (1.1.0); absent on a stripped build -> the 🎤 button hides itself
    from PySide6.QtMultimedia import QAudioSource, QAudioFormat, QMediaDevices
    HAVE_MIC = True
except Exception:   # noqa: BLE001
    HAVE_MIC = False

# Hands-free voice: when to treat the mic as speech vs silence (RMS of 16-bit samples, 0..32768).
VAD_START_RMS = 450      # above this = you're talking
VAD_STOP_RMS = 250       # below this = quiet
VAD_SILENCE_MS = 1200    # end the turn after this much trailing quiet (once you've spoken)
VAD_MAX_MS = 15000       # hard cap on one listening turn, so it can't listen forever
WAKE_RE = re.compile(r"\b(?:hey\s+|ok(?:ay)?\s+)?golem\b[\s,.:!?-]*", re.I)   # "Hey Golem", "ok golem", "golem"

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


# ---------------------------------------------------------------- voice (0.9.0; standalone Piper in 1.0.5)
#
# The agent reads its replies aloud with the self-contained Piper binary (piper.exe - NO Python). The
# Builder's Add-ons -> Voice installs the engine + default voice on a clean machine; here we just use it,
# and fall back to downloading a voice model on demand if the user picked one that isn't here yet.
PIPER_DIR = AGENT_DIR / "piper"
PIPER_VOICES_DIR = PIPER_DIR / "voices"
PIPER_EXE = PIPER_DIR / ("piper.exe" if os.name == "nt" else "piper")
PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
DEFAULT_VOICE = "en_US-hfc_female-medium"   # more natural than lessac (which sounded robotic)


def piper_available() -> bool:
    """True when the standalone Piper binary is installed - no Python needed."""
    return PIPER_EXE.exists()


_VOICE_RE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)$")


def _safe_voice(voice: str) -> str:
    """Voice names go into a path and a URL; keep them to Piper's shape so a crafted name can't escape
    the voices folder. Fall back to the default if it looks wrong."""
    v = (voice or "").strip()
    return v if _VOICE_RE.match(v) else DEFAULT_VOICE


def _voice_url(voice: str, ext: str) -> str:
    """HuggingFace URL for a voice file, derived from the Piper voice name."""
    lang, speaker, quality = voice.split("-", 2)
    return f"{PIPER_VOICES_BASE}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{voice}{ext}"


def ensure_voice(voice: str) -> bool:
    """Make sure the voice model (.onnx + .onnx.json) is downloaded. True when present and usable."""
    voice = _safe_voice(voice)
    onnx = PIPER_VOICES_DIR / f"{voice}.onnx"
    cfg = PIPER_VOICES_DIR / f"{voice}.onnx.json"
    if onnx.exists() and cfg.exists():
        return True
    try:
        PIPER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(_voice_url(voice, ".onnx.json"), cfg)
        urllib.request.urlretrieve(_voice_url(voice, ".onnx"), onnx)
    except Exception:
        try:
            onnx.unlink(missing_ok=True)   # don't leave a half file that looks installed
        except Exception:
            pass
        return False
    return onnx.exists() and cfg.exists()


_MD = re.compile(r"[`*#_>~\[\]]|\(https?://[^)]+\)")
_BRACKET = re.compile(r"\[[^\]]*\]")                         # [brain: ...], [Paused ...] - status, never spoken


def _clean_for_speech(text: str) -> str:
    t = ANSI.sub("", text)                                   # strip any colour codes
    t = re.sub(r"```.*?```", " (code) ", t, flags=re.S)      # don't read code blocks aloud
    t = _BRACKET.sub(" ", t)                                 # drop bracketed status notices (brain/model/etc.)
    t = _MD.sub("", t).strip()
    if len(t) > 700:                                         # trim long replies at a natural boundary
        cut = t[:700]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
        t = cut[:end + 1] if end > 400 else cut.rsplit(" ", 1)[0]
    return t


def _wav_envelope(path: Path, bucket: float = 0.06):
    """Return (duration_seconds, [loudness 0..1 per bucket]) for a 16-bit PCM WAV - the shape of the
    voice, so the orb can pulse its light in time with the speech."""
    try:
        import wave
        import array
        with wave.open(str(path), "rb") as w:
            rate, ch, sw, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(frames)
    except Exception:
        return 0.0, []
    dur = frames / rate if rate else 0.0
    if sw != 2 or rate <= 0:                 # Piper emits 16-bit PCM; skip the envelope for anything else
        return dur, []
    samples = array.array("h")
    samples.frombytes(raw)
    if ch > 1:
        samples = samples[::ch]              # one channel is plenty for a loudness envelope
    per = max(1, int(rate * bucket))
    amps = []
    for i in range(0, len(samples), per):
        chunk = samples[i:i + per]
        if not chunk:
            break
        amps.append((sum(v * v for v in chunk) / len(chunk)) ** 0.5)
    peak = max(amps) if amps else 0.0
    amps = [round((a / peak) ** 0.7, 3) for a in amps] if peak else []   # gamma lifts quiet syllables
    return dur, amps


def _signal_orb_speaking(dur: float, amps: list, bucket: float = 0.06):
    """Tell the ambient orb the voice is playing and hand it the loudness envelope, so it pulses with
    light in sync. Best-effort: the orb may not be running."""
    try:
        now = time.time()
        lines = [str(now + (dur if dur > 0 else 2.5) + 0.25)]
        if amps:
            lines.append("SPEAK %.3f %.3f %s" % (now, bucket, " ".join(str(a) for a in amps)))
        (Path(tempfile.gettempdir()) / "golem_orb_signal").write_text("\n".join(lines))
    except Exception:
        pass


class VoicePlayer(QThread):
    """Synthesize text with Piper and play it, off the UI thread so the window never freezes."""
    done = Signal()

    def __init__(self, text: str, voice: str):
        super().__init__()
        self.text, self.voice = text, _safe_voice(voice)

    def run(self):
        wav = None
        try:
            if not piper_available() or not self.text.strip():
                return
            if not ensure_voice(self.voice):   # download the voice model the first time it's used
                return
            model = PIPER_VOICES_DIR / f"{self.voice}.onnx"
            line = " ".join(self.text.split())   # one utterance - Piper treats each input line separately
            wav = Path(tempfile.gettempdir()) / f"golem-say-{int(time.time() * 1000)}.wav"
            subprocess.run([str(PIPER_EXE), "-m", str(model), "-f", str(wav)],
                           input=line, text=True, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=300, cwd=str(PIPER_DIR),
                           creationflags=CREATE_NO_WINDOW)
            if wav.exists() and wav.stat().st_size > 100 and os.name == "nt":
                dur, amps = _wav_envelope(wav)          # the voice's shape, for the orb to pulse with
                _signal_orb_speaking(dur, amps)         # hand it to the orb just before the sound starts
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


def _write_wav16(path: Path, pcm: bytes):
    """Write raw 16 kHz mono 16-bit PCM bytes as a WAV (what whisper.cpp wants)."""
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)


class MicRecorder:
    """Record the default microphone to 16 kHz mono 16-bit PCM, on the GUI thread (event-driven)."""

    def __init__(self):
        self._src = None
        self._io = None
        self._buf = bytearray()
        self._level = 0.0

    def start(self) -> bool:
        if not HAVE_MIC:
            return False
        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            dev = QMediaDevices.defaultAudioInput()
            if dev is None or dev.isNull():
                return False
            self._buf = bytearray()
            self._src = QAudioSource(dev, fmt)
            self._io = self._src.start()
            if self._io is None:
                self._src = None
                return False
            self._io.readyRead.connect(self._on_ready)
            return True
        except Exception:   # noqa: BLE001
            self._src = self._io = None
            return False

    def _on_ready(self):
        try:
            data = bytes(self._io.readAll())
        except Exception:   # noqa: BLE001
            return
        if not data:
            return
        self._buf += data
        try:   # track loudness (RMS) of the latest chunk, for silence detection
            import array
            s = array.array("h")
            s.frombytes(data[: len(data) // 2 * 2])
            if s:
                self._level = (sum(v * v for v in s) / len(s)) ** 0.5
        except Exception:   # noqa: BLE001
            pass

    def level(self) -> float:
        return self._level

    def is_recording(self) -> bool:
        return self._src is not None

    def stop(self) -> bytes:
        try:
            if self._src:
                self._src.stop()
        except Exception:   # noqa: BLE001
            pass
        data = bytes(self._buf)
        self._src = self._io = None
        self._buf = bytearray()
        self._level = 0.0
        return data


class TranscribeThread(QThread):
    """Run whisper.cpp on a recorded WAV off the UI thread, then delete the temp file."""
    done = Signal(str)

    def __init__(self, wav: Path):
        super().__init__()
        self.wav = wav

    def run(self):
        text = ""
        try:
            import agent
            text = agent.whisper_transcribe(self.wav)
        except Exception:   # noqa: BLE001
            text = ""
        finally:
            try:
                self.wav.unlink(missing_ok=True)
            except Exception:   # noqa: BLE001
                pass
        self.done.emit(text)


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
        self.speak_on = bool(vc.get("enabled", True))
        self.voice_name = vc.get("voice") or DEFAULT_VOICE
        self._voice_thread = None
        self._spoken_text = ""        # last text spoken aloud, so a reply isn't read twice
        self._warned_no_piper = False
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.timeout.connect(self._speak_current)
        self._mic = MicRecorder()          # talk-to-Golem: record the mic, whisper transcribes it
        self._transcribe = None
        self._convo = False                # hands-free conversation mode (mic button on)
        self._wake = bool(vc.get("wake", False))   # passive "Hey Golem" wake-word listening (opt-in)
        self._convo_empty = 0              # consecutive silent/empty turns, so noise can't loop forever
        self._heard_speech = False
        self._silence_since = 0.0
        self._rec_start = 0.0
        self._last_chunk_time = 0.0        # when the agent last produced output (to know it's still replying)
        self._vad_timer = QTimer(self)     # watches mic level to end a turn when you stop talking
        self._vad_timer.timeout.connect(self._vad_tick)
        self._convo_timer = QTimer(self)   # re-opens the mic for the next turn once Golem is idle
        self._convo_timer.timeout.connect(self._convo_poll)
        self._orb_req = Path(tempfile.gettempdir()) / "golem_orb_request"   # orb -> window: "talk to me"
        self._orb_req_seen = ""
        self._show_req = Path(tempfile.gettempdir()) / "golem_show_request"  # a 2nd launch -> "come to front"
        self._show_req_seen = ""
        self._orb_req_timer = QTimer(self)
        self._orb_req_timer.timeout.connect(self._orb_req_poll)
        self._orb_req_timer.start(400)

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
        root.addWidget(self._routines_bar())
        root.addWidget(self._composer())

        self.start_agent()
        if _config().get("orb_autostart", True):   # the orb is standard - on unless explicitly turned off
            self._launch_orb()
        if self._wake and HAVE_MIC:                 # listen for "Hey Golem" in the background, if enabled
            QTimer.singleShot(1500, self._start_wake)
        self._tray_presence = bool(_config().get("tray_presence", False))
        self._really_quit = False
        self._tray = None
        if self._tray_presence:
            self._setup_tray()
        if _config().get("first_run", False):       # a warm spoken hello the very first time
            QTimer.singleShot(1400, self._first_run_greeting)
        _launch = [r for r in (_config().get("routines") or []) if r.get("at_launch")]
        if _launch:                                  # run any "run when Golem starts" routines
            QTimer.singleShot(2600, lambda: self._run_launch_routines(_launch))

        self._last_user_activity = 0.0               # when you last typed/sent (so check-ins stay out of the way)
        self._sched_ran = {}                         # routine name -> date it last ran today (scheduled routines)
        self._sched_timer = QTimer(self)             # fires scheduled routines at their daily time
        self._sched_timer.timeout.connect(self._check_schedule)
        self._sched_timer.start(60000)               # once a minute
        pc = _config().get("proactive") or {}
        self._proactive = bool(pc.get("enabled", False))   # Navi mode: glance at the screen and pipe up
        if self._proactive:
            mins = max(3, int(pc.get("interval_min", 15) or 15))
            self._proactive_timer = QTimer(self)
            self._proactive_timer.timeout.connect(self._proactive_check)
            self._proactive_timer.start(mins * 60000)

    def _check_schedule(self):
        """Run any routine whose daily time matches now (while Golem is running)."""
        if not self._running():
            return
        now = time.localtime()
        hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        today = f"{now.tm_year}-{now.tm_yday}"
        for r in (_config().get("routines") or []):
            at = (r.get("at") or "").strip()
            name = r.get("name") or ""
            if at == hhmm and self._sched_ran.get(name) != today:
                self._sched_ran[name] = today
                self._run_routine(r.get("prompt") or "")

    def _proactive_check(self):
        """Navi mode: glance at the screen and offer one helpful line - but only when you're not busy."""
        if not self._proactive or not self._running():
            return
        if self._mic.is_recording() or self._transcribe is not None or self._convo:
            return
        if self._voice_thread and self._voice_thread.isRunning() or self._speak_timer.isActive():
            return
        if (time.time() - self._last_chunk_time) < 3:            # agent still replying
            return
        if (time.time() - self._last_user_activity) < 90:        # you're actively using it - don't butt in
            return
        try:
            pix = QApplication.primaryScreen().grabWindow(0)
            out = Path(tempfile.gettempdir()) / f"golem-check-{int(time.time())}.png"
            pix.save(str(out), "PNG")
            self._stage(str(out))
        except Exception:   # noqa: BLE001
            return
        self._last_chunk_time = time.time()
        self.entry.setText("[check-in] Glance at my screen. In ONE short, friendly sentence point out "
                           "anything useful I could do right now (an error to fix, something stuck, a tip). "
                           "If nothing stands out, just say 'All clear.'")
        self.send()

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
            self._bubble("To hear replies, open the Golem Builder, go to the Add-ons tab, and click "
                         "Install under Voice (a quick one-time download, no setup).", "system")
        self._save_voice_cfg()

    def _save_voice_cfg(self):
        try:
            cfg = _config()
            cfg["voice"] = {"enabled": self.speak_on, "voice": self.voice_name, "wake": self._wake}
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------------------------------------------------------------- talk to Golem (1.1.0, speech to text)

    def _toggle_listen(self, on: bool):
        """The mic button turns a hands-free CONVERSATION on/off: Golem listens, you speak, it detects
        when you stop, transcribes, answers (aloud), and listens again - until you click the mic off."""
        if on:
            try:
                import agent
                ready = agent.whisper_installed()
            except Exception:   # noqa: BLE001
                ready = False
            if not ready:
                self.listen_btn.setChecked(False)
                self._bubble("To talk to Golem, open the Golem Builder, go to the Add-ons tab, and click "
                             "Install under 'Talk to Golem' (a one-time download).", "system")
                return
            if not self._running():
                self.listen_btn.setChecked(False)
                self._bubble("Start the agent first - click Restart.", "system")
                return
            self._convo = True
            self._convo_empty = 0
            if not self.speak_on:               # a voice conversation needs voice coming back
                self.speak_btn.setChecked(True)   # fires _toggle_speak(True)
            self._convo_timer.start(400)
            self._start_listen_turn()
        else:
            self._stop_speech()                 # clicking the mic off also shuts Golem up (manual interrupt)
            self._convo = False
            self._convo_empty = 0
            self._end_listen_turn(discard=True)
            if self._wake:
                self._start_wake()              # drop back to passively waiting for "Hey Golem"
            else:
                self._convo_timer.stop()

    def _start_wake(self):
        """Begin passive 'Hey Golem' listening: the mic stays open, whisper only runs when you speak,
        and a conversation starts only when your words contain the wake word."""
        if not self._wake or not HAVE_MIC:
            return
        self._convo_timer.start(400)
        self._start_listen_turn()

    def _listening(self) -> bool:
        return self._wake or self._convo

    def _start_listen_turn(self):
        """Open the mic for one turn (conversation or passive wake), if Golem isn't talking/working."""
        if not self._listening() or self._mic.is_recording() or self._transcribe is not None:
            return
        if not self._running():
            return
        if not self._mic.start():
            self._convo = self._wake = False
            self._convo_timer.stop()
            if HAVE_MIC:
                self.listen_btn.setChecked(False)
            self._bubble("Couldn't open the microphone. Check that one is plugged in and that Windows lets "
                         "apps use it (Settings - Privacy - Microphone).", "system")
            return
        self._heard_speech = False
        self._silence_since = 0.0
        self._rec_start = time.time()
        if HAVE_MIC:
            self.listen_btn.setText("●")
        self.sub.setText("listening for 'Hey Golem'..." if (self._wake and not self._convo) else "listening...")
        self._signal_orb_listening()
        self._vad_timer.start(120)

    def _vad_tick(self):
        if not self._mic.is_recording():
            self._vad_timer.stop()
            return
        self._signal_orb_listening()
        lvl = self._mic.level()
        now = time.time()
        if lvl >= VAD_START_RMS:
            self._heard_speech = True
            self._silence_since = 0.0
        elif lvl < VAD_STOP_RMS and self._heard_speech:
            if self._silence_since == 0.0:
                self._silence_since = now
            elif (now - self._silence_since) * 1000 >= VAD_SILENCE_MS:
                self._end_listen_turn()
                return
        if (now - self._rec_start) * 1000 >= VAD_MAX_MS:
            self._end_listen_turn()

    def _end_listen_turn(self, discard: bool = False):
        """Stop recording. If something was said, transcribe it; else keep waiting (convo bails after a few)."""
        self._vad_timer.stop()
        recording = self._mic.is_recording()
        pcm = self._mic.stop() if recording else b""
        if HAVE_MIC:
            self.listen_btn.setText("●" if self._listening() else "🎤")
        self.sub.setText("")
        if discard or not recording:
            return
        if not self._heard_speech or len(pcm) < int(16000 * 2 * 0.3):
            if self._convo:
                self._convo_empty += 1
                if self._convo_empty >= 3:
                    self._end_conversation("I didn't hear anything, so I stopped the conversation. "
                                           "Click the mic to talk again.")
                    return
            return            # wake mode just keeps waiting; the poller re-opens the mic
        self._convo_empty = 0
        wav = Path(tempfile.gettempdir()) / f"golem-hear-{int(time.time() * 1000)}.wav"
        try:
            _write_wav16(wav, pcm)
        except Exception:   # noqa: BLE001
            return
        self.sub.setText("transcribing...")
        self._transcribe = TranscribeThread(wav)
        self._transcribe.done.connect(self._heard)
        self._transcribe.start()

    def _heard(self, text: str):
        self._transcribe = None
        self.sub.setText("")
        text = (text or "").strip()
        if self._convo:
            if not text:
                self._convo_empty += 1
                if self._convo_empty >= 3:
                    self._end_conversation("Didn't catch that. Click the mic to talk again.")
                return                # convo poller re-opens the mic
            self._convo_empty = 0
            self._last_chunk_time = time.time()   # reply in-flight, so we don't re-listen yet
            self.entry.setText(text)
            self.send()               # hands-free: your words go in and send
            return
        if self._wake:                            # passive: only act if you said the wake word
            if not text:
                return
            m = WAKE_RE.search(text)
            if not m:
                return                            # not addressed to Golem - keep listening
            self._convo = True
            self._convo_empty = 0
            if not self.speak_on:                 # a spoken conversation needs voice back
                self.speak_btn.setChecked(True)
            if HAVE_MIC:
                self.listen_btn.setChecked(True)
            remainder = text[m.end():].strip()    # anything you said after "Hey Golem"
            if remainder:
                self._last_chunk_time = time.time()
                self.entry.setText(remainder)
                self.send()
            else:
                self._bubble("(listening - go ahead)", "system")

    def _convo_poll(self):
        """Re-open the mic for the next turn once Golem has finished replying and speaking."""
        if not self._listening():
            self._convo_timer.stop()
            return
        if self._mic.is_recording() or self._transcribe is not None:
            return
        if self._voice_thread and self._voice_thread.isRunning():
            return                # Golem is speaking - don't listen to itself
        if self._speak_timer.isActive():
            return                # a reply is queued to be spoken
        if self._convo and (time.time() - self._last_chunk_time) < 1.1:
            return                # Golem is still producing its reply
        self._start_listen_turn()

    def _end_conversation(self, msg: str = ""):
        """End the active back-and-forth. If wake-word listening is on, drop back to passively waiting."""
        self._convo = False
        self._vad_timer.stop()
        try:
            self._mic.stop()
        except Exception:   # noqa: BLE001
            pass
        if HAVE_MIC:
            self.listen_btn.setChecked(self._wake)
            self.listen_btn.setText("●" if self._wake else "🎤")
        self.sub.setText("")
        if msg:
            self._bubble(msg, "system")
        if not self._wake:
            self._convo_timer.stop()

    def _stop_speech(self):
        """Cut off Golem mid-sentence (manual interrupt) - click the mic/orb to shut it up."""
        try:
            if os.name == "nt":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:   # noqa: BLE001
            pass

    def _orb_req_poll(self):
        """Watch the little cross-process signal files: the orb's 'Talk to Golem' action, and a second
        launch asking the running Golem to come to the front."""
        try:
            sv = self._show_req.read_text().strip()
            if sv and sv != self._show_req_seen:
                self._show_req_seen = sv
                self._show_from_tray()                # a 2nd launch pinged us - surface the existing window
        except Exception:   # noqa: BLE001
            pass
        try:
            val = self._orb_req.read_text().strip()
        except Exception:   # noqa: BLE001
            return
        if not val or val == self._orb_req_seen:
            return
        self._orb_req_seen = val
        if not HAVE_MIC or not self._running():
            return
        new = not self._convo                         # orb click toggles the conversation
        self.listen_btn.setChecked(new)               # visual only (clicked signal doesn't fire on this)
        self._toggle_listen(new)                      # so drive it ourselves

    def _setup_tray(self):
        """A system-tray presence so closing the window keeps Golem running (ready + listening)."""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self._tray = QSystemTrayIcon(QIcon(str(ICON)) if ICON.exists() else self.windowIcon(), self)
            self._tray.setToolTip(f"{self.name} - Golem")
            m = QMenu()
            a_show = QAction("Show Golem", m)
            a_show.triggered.connect(self._show_from_tray)
            a_talk = QAction("Talk to Golem", m)
            a_talk.triggered.connect(self._talk_from_tray)
            a_quit = QAction("Quit Golem", m)
            a_quit.triggered.connect(self._quit_fully)
            m.addAction(a_show)
            m.addAction(a_talk)
            m.addSeparator()
            m.addAction(a_quit)
            self._tray.setContextMenu(m)
            self._tray.activated.connect(
                lambda r: self._show_from_tray() if r == QSystemTrayIcon.Trigger else None)
            self._tray.show()
        except Exception:   # noqa: BLE001
            self._tray = None

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _talk_from_tray(self):
        self._show_from_tray()
        if HAVE_MIC and not self._convo:
            self.listen_btn.setChecked(True)
            self._toggle_listen(True)

    def _quit_fully(self):
        self._really_quit = True
        self.close()

    def _first_run_greeting(self):
        """The very first launch: Golem introduces itself, speaks the hello, and the orb pulses a welcome."""
        cfg = _config()
        if not cfg.get("first_run", False):
            return
        cfg["first_run"] = False
        try:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:   # noqa: BLE001
            pass
        msg = (f"Hi - I'm {self.name}. I live right here on your computer, and everything we do stays on "
               "this PC - nothing is sent anywhere. Tell me what you'd like to do, or just ask me anything.")
        self._bubble(msg, "agent")
        self._ping_orb(3.0)
        if self.speak_on and piper_available():
            self._voice_thread = VoicePlayer(_clean_for_speech(msg), self.voice_name)
            self._voice_thread.start()

    def _signal_orb_listening(self):
        """Tell the orb it's listening, so it shows a distinct state (a cool pulsing ring), not speaking."""
        try:
            now = time.time()
            (Path(tempfile.gettempdir()) / "golem_orb_signal").write_text(f"{now + 1.0}\nLISTEN {now + 1.0}")
        except Exception:   # noqa: BLE001
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
        if HAVE_MIC:
            self.listen_btn = QPushButton("🎤")
            self.listen_btn.setCheckable(True)
            self.listen_btn.setCursor(Qt.PointingHandCursor)
            self.listen_btn.setToolTip("Talk to Golem - click to start, click again to send")
            self.listen_btn.setStyleSheet(
                "QPushButton { background:transparent; border:1px solid #2a2f3a; border-radius:8px; "
                "padding:6px 11px; font-size:15px; }"
                " QPushButton:checked { background:#e5484d33; border-color:#e5484d; }")
            self.listen_btn.clicked.connect(self._toggle_listen)   # clicked = user only (setChecked won't loop)
            h.addWidget(self.listen_btn)
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
        self.undo_btn = QPushButton("↶ Undo")
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.setToolTip("See the files Golem changed and put any of them back")
        self.undo_btn.setStyleSheet(
            f"color:{MUTED}; background:transparent; border:1px solid #2a2f3a; "
            "border-radius:8px; padding:6px 12px;")
        self.undo_btn.clicked.connect(self._open_undo)
        h.addWidget(self.undo_btn)
        self.restart_btn = QPushButton("Restart")
        self.restart_btn.setCursor(Qt.PointingHandCursor)
        self.restart_btn.setStyleSheet(
            f"color:{MUTED}; background:transparent; border:1px solid #2a2f3a; "
            "border-radius:8px; padding:6px 12px;")
        self.restart_btn.clicked.connect(self.restart)
        h.addWidget(self.restart_btn)
        return bar

    def _open_undo(self):
        """Trust & undo: list the files Golem changed and let the user restore any one."""
        try:
            import agent
            changes = agent.recent_changes(40)
        except Exception:   # noqa: BLE001
            changes = []
        dlg = QDialog(self)
        dlg.setWindowTitle("Recent changes - undo")
        v = QVBoxLayout(dlg)
        if not changes:
            v.addWidget(QLabel("Golem hasn't changed any files yet. When it does, they'll show here and "
                               "you can put any of them back with one click."))
        else:
            v.addWidget(QLabel("Files Golem changed, newest first. Undo restores the file to how it was "
                               "just before that change."))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            holder = QWidget()
            hv = QVBoxLayout(holder)
            for c in changes:
                row = QHBoxLayout()
                lbl = QLabel(f"{Path(c['path']).name}   ·   {c.get('time', '')}")
                lbl.setToolTip(c['path'])
                btn = QPushButton("Undo")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _=False, b=c['backup'], w=dlg: self._do_undo(b, w))
                row.addWidget(lbl, 1)
                row.addWidget(btn)
                rw = QWidget()
                rw.setLayout(row)
                hv.addWidget(rw)
            hv.addStretch(1)
            scroll.setWidget(holder)
            scroll.setMinimumSize(480, 320)
            v.addWidget(scroll)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    def _do_undo(self, backup: str, dlg):
        try:
            import agent
            msg = agent.undo_change(backup)
        except Exception as e:   # noqa: BLE001
            msg = f"Couldn't undo: {e}"
        self._bubble(msg, "system")
        dlg.accept()

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
        self.look_btn = self._icon_button("👁", "Look at my screen - Golem sees it and tells you what's there")
        self.look_btn.clicked.connect(self.look_at_screen)
        h.addWidget(self.look_btn)

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
            try:
                if os.name == "nt":
                    # golem-core.exe is a PyInstaller one-file bootloader that runs the real worker as a
                    # child, so terminate() on the bootloader leaves the worker (and anything it started)
                    # alive in the background. Kill the whole process tree so nothing lingers.
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                else:
                    self.proc.terminate()
                self.proc.wait(5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
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
        self._last_chunk_time = time.time()   # agent is producing output - hands-free waits for it to finish
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

    def _routines_bar(self) -> QWidget:
        """A row of one-click buttons for the user's saved routines (quick tasks), above the composer."""
        self._routines_wrap = QWidget()
        self._routines_lay = QHBoxLayout(self._routines_wrap)
        self._routines_lay.setContentsMargins(16, 0, 16, 0)
        self._routines_lay.setSpacing(6)
        self._build_routine_buttons()
        return self._routines_wrap

    def _build_routine_buttons(self):
        lay = self._routines_lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        routines = _config().get("routines") or []
        if not routines:
            self._routines_wrap.setVisible(False)
            return
        self._routines_wrap.setVisible(True)
        lbl = QLabel("Quick:")
        lbl.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        lay.addWidget(lbl)
        for r in routines[:8]:
            name = ((r.get("name") or "").strip() or "Task")[:24]
            b = QPushButton(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton { background:#1b1f27; color:#c7ccda; border:1px solid #2a2f3a; "
                            "border-radius:12px; padding:5px 12px; } QPushButton:hover { background:#232834; }")
            b.clicked.connect(lambda _=False, pr=(r.get("prompt") or ""): self._run_routine(pr))
            lay.addWidget(b)
        lay.addStretch(1)

    def _run_routine(self, prompt: str):
        prompt = (prompt or "").strip()
        if not self._running():
            self._bubble("The agent isn't running - click Restart.", "system")
            return
        if not prompt:
            return
        self.entry.setText(prompt)
        self.send()

    def _run_launch_routines(self, routines: list):
        if not routines or not self._running():
            return
        self._run_routine(routines[0].get("prompt") or "")
        rest = routines[1:]
        if rest:
            QTimer.singleShot(5000, lambda: self._run_launch_routines(rest))

    def look_at_screen(self):
        """One tap: grab the screen and immediately ask Golem what's there (screen awareness)."""
        if not self._running():
            self._bubble("The agent isn't running - click Restart.", "system")
            return
        self.showMinimized()
        QTimer.singleShot(350, self._grab_and_ask)

    def _grab_and_ask(self):
        try:
            pix = QApplication.primaryScreen().grabWindow(0)
            out = Path(tempfile.gettempdir()) / f"golem-shot-{int(time.time())}.png"
            pix.save(str(out), "PNG")
            self._stage(str(out))
            staged = True
        except Exception as e:  # noqa: BLE001
            self._bubble(f"Could not see the screen: {e}", "system")
            staged = False
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if staged:
            self.entry.setText("What's on my screen right now? Describe it briefly and point out anything "
                               "useful I could do.")
            self.send()

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
        self._last_chunk_time = time.time()   # reply incoming - keep hands-free from re-listening too soon
        self._last_user_activity = time.time()   # you're active - proactive check-ins hold off
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
        if getattr(self, "_tray_presence", False) and self._tray is not None and not self._really_quit:
            event.ignore()                 # keep Golem running in the tray instead of quitting
            self.hide()
            try:
                self._tray.showMessage("Golem", "Still here in the tray - click to come back, or right-click "
                                       "to quit.", QSystemTrayIcon.Information, 3000)
            except Exception:   # noqa: BLE001
                pass
            return
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
        self._terminate()   # kill the agent core (whole tree) first, so nothing is left orphaned
        event.accept()
        os._exit(0)         # hard-exit so Golem.exe never lingers in the background after you close it


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


def _single_instance() -> bool:
    """True if we're the first instance. If another Golem window is already running, ask it to come to
    the front (via a tiny signal file it polls) and return False so this copy exits - no duplicate agent
    cores or orbs from relaunching while Golem sits in the tray."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.CreateMutexW(None, False, "Golem_Agent_SingleInstance")
        if k.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            try:
                (Path(tempfile.gettempdir()) / "golem_show_request").write_text(str(time.time()))
            except Exception:   # noqa: BLE001
                pass
            return False
    except Exception:   # noqa: BLE001
        return True      # if the check itself fails, don't block the app
    return True


def main():
    if not _single_instance():
        return           # another Golem is already running; we asked it to show and now bow out
    app = QApplication(sys.argv)
    win = AgentWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
