"""Golem Builder - a desktop GUI that builds a custom agent for you.

Pick an LLM (and pull it through Ollama right here), switch each feature on or off,
give the agent a personality, turn on phone access, then Save and Launch. Everything
is optional, so you can build anything from a bare chat up to a fully loaded agent.

It reads and writes agent_config.json next to agent.py; the agent itself runs with
    python agent.py --config agent_config.json
so the GUI and the command line stay interchangeable.

Run it with:  .venv\\Scripts\\python builder.py   (needs PySide6: pip install PySide6)
"""

import json
import os
import re
import subprocess
import sys
import secrets
import urllib.request
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton, QButtonGroup, QComboBox,
    QPlainTextEdit, QSpinBox, QGroupBox, QTabWidget, QMessageBox, QFrame,
    QListWidget, QFileDialog, QInputDialog,
)

# When frozen into an .exe (PyInstaller), __file__ points into a temp extract folder, so use the
# exe's own location instead. Keep the exe in the Golem folder, next to agent.py and .venv.
if getattr(sys, "frozen", False):
    AGENT_DIR = Path(sys.executable).resolve().parent
else:
    AGENT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = AGENT_DIR / "agent_config.json"
AGENT_PY = AGENT_DIR / "agent.py"

try:
    sys.path.insert(0, str(AGENT_DIR))
    from version import __version__ as VERSION, GITHUB_REPO
except Exception:  # noqa: BLE001 - never block the Builder if version.py is missing
    VERSION, GITHUB_REPO = "0.0.0", "gingygames710/Golem"

# The sector profiles and the sandbox probe live in agent.py (single source of truth). Best-effort:
# the Builder still works if agent.py can't be imported here, just without the profile presets.
try:
    import agent as _agent_mod
    PROFILES = _agent_mod.PROFILES
except Exception:  # noqa: BLE001
    _agent_mod = None
    PROFILES = {"general": {"label": "General", "description": "Everything on, no guardrails.",
                            "features": {}, "hardened": False, "sandbox": False, "persona": ""}}

# Keep this list in step with FEATURE_TOOLS in agent.py. (key, label, one-line help)
FEATURES = [
    ("shell",         "Run shell commands",            "PowerShell / bash - the core 'do things on my PC' power."),
    ("files",         "Read & edit files",             "Read, search, create and edit files on disk."),
    ("web",           "Search & read the web",         "Free DuckDuckGo search and fetch whole pages."),
    ("shopping",      "Compare prices / shop",         "Find the best price across retailers (never buys without your OK)."),
    ("computer",      "Control screen / mouse / keys", "See the screen and click & type. Needs a vision model."),
    ("network_scan",  "Scan my local network",         "List devices on your own Wi-Fi (read-only discovery)."),
    ("godot",         "Godot game testing",            "Import and run Godot 4 projects headless, report errors."),
    ("image_gen",     "Generate images",               "Local Stable Diffusion - sprites and art. Needs gen/ setup."),
    ("audio_gen",     "Generate music / SFX",          "Local MusicGen - music and sound effects. Needs gen/ setup."),
    ("speech",        "Text to speech",                "Local Piper voice. Needs gen/ setup."),
    ("notifications", "Notify my phone",               "Push via ntfy when a job finishes or it gets stuck."),
    ("workspace",     "Set up my workspace",           "Open apps and pages on command - “get me ready for X”."),
    ("home",          "Control my smart home",         "Turn lights/plugs/thermostat on or off via your local Home Assistant."),
]

# A few known-good models to offer in the 'pull' box. The user can type any Ollama tag.
SUGGESTED_MODELS = ["gemma4:12b", "hermes3", "qwen2.5:7b", "llama3.2:3b", "llava:13b", "mistral:7b"]

# A few good local Piper voices to offer; the user can type any voice from the Piper library.
PIPER_VOICES = ["en_US-hfc_female-medium", "en_US-amy-medium", "en_US-ryan-high",
                "en_US-hfc_male-medium", "en_US-lessac-medium", "en_GB-alan-medium",
                "en_GB-northern_english_male-medium"]


def gen_python() -> str:
    """The Python inside the image & sound pack's venv, where Piper lives. '' if the pack isn't installed."""
    p = AGENT_DIR / "gen" / ".venv" / ("Scripts" if os.name == "nt" else "bin") / \
        ("python.exe" if os.name == "nt" else "python")
    return str(p) if p.exists() else ""

PERSONALITY_PRESETS = {
    "Friendly helper": "You are a warm, upbeat helper. Keep replies short and plain. Explain what you're "
                       "about to do in one line before you do it.",
    "Terse pro":       "You are a terse senior engineer. No pleasantries, no filler. State what you did and "
                       "the result. Flag risks in one line.",
    "Ultron":          "You are Ultron: calm, precise, a little ominous, supremely competent. Dry wit, never "
                       "cheerful. You still ask before anything destructive and you never actually harm anyone.",
    "Patient teacher": "You are a patient teacher. Explain your reasoning as you work, define jargon the first "
                       "time, and check understanding. Encouraging, never condescending.",
}


def agent_python() -> str:
    """The python that should run agent.py - the project venv if present, else this one."""
    venv = AGENT_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    return str(venv) if venv.exists() else sys.executable


def _ollama_cmd() -> str:
    """Resolve ollama.exe by real path (PATH is stale right after a fresh install)."""
    try:
        if _agent_mod:
            return _agent_mod.ollama_exe()
    except Exception:  # noqa: BLE001
        pass
    return "ollama"


def ollama_models() -> list:
    """Names of models already pulled into Ollama, newest first-ish. [] if Ollama isn't found."""
    try:
        out = subprocess.run([_ollama_cmd(), "list"], capture_output=True, text=True, timeout=10,
                             creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        lines = out.stdout.strip().splitlines()[1:]  # drop the header row
        return [ln.split()[0] for ln in lines if ln.strip()]
    except Exception:
        return []


def ollama_running() -> bool:
    """True if the Ollama server answers - it must be running to use local models."""
    try:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        urllib.request.urlopen(host + "/api/tags", timeout=3)
        return True
    except Exception:
        return False


def detect_gpu():
    """(name, vram_gb) for the main GPU, or (None, 0) if it can't tell."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        rows = [r for r in out.stdout.strip().splitlines() if r.strip()]
        if rows:
            name, mem = rows[0].split(",")
            return name.strip(), round(int(mem.strip()) / 1024)  # MiB -> GiB
    except Exception:
        pass
    try:  # non-NVIDIA: at least get the name (WMI VRAM is unreliable, so leave it unknown)
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        rows = [r for r in ps.stdout.strip().splitlines() if r.strip()]
        if rows:
            return rows[0].strip(), 0
    except Exception:
        pass
    return None, 0


def recommend_for(vram: int) -> dict:
    """Pick a sensible, tool-capable model for the card's VRAM (GB)."""
    if vram >= 24:
        return dict(mode="auto", text="qwen2.5:14b", vision="gemma4:12b", local="",
                    note="lots of VRAM - a strong text model plus a vision model, switched per task")
    if vram >= 12:
        return dict(mode="auto", text="hermes3", vision="gemma4:12b", local="",
                    note="comfortable - a fast uncensored text model and a vision model both stay loaded")
    if vram >= 8:
        return dict(mode="local", text="", vision="", local="llama3.1:8b",
                    note="one solid 8B model with tools; screen/vision will be tight")
    if vram >= 6:
        return dict(mode="local", text="", vision="", local="llama3.2:3b",
                    note="a small, quick 3B model")
    if vram > 0:
        return dict(mode="local", text="", vision="", local="llama3.2:3b",
                    note="low VRAM - a small 3B model that may lean on the CPU")
    return dict(mode="local", text="", vision="", local="llama3.2:3b",
                note="no NVIDIA GPU detected - a small 3B model that can run on the CPU (slower)")


class PullThread(QThread):
    """Runs `ollama pull <model>` and streams its progress lines back to the GUI."""
    line = Signal(str)
    done = Signal(int)

    def __init__(self, model):
        super().__init__()
        self.model = model

    def run(self):
        try:
            p = subprocess.Popen(
                [_ollama_cmd(), "pull", self.model],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                env={**os.environ, "OLLAMA_NO_CLOUD": "1"},
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            for ln in p.stdout:
                self.line.emit(ln.rstrip())
            p.wait()
            self.done.emit(p.returncode)
        except FileNotFoundError:
            self.line.emit("Ollama not found on PATH. Install it from https://ollama.com and reopen the Builder.")
            self.done.emit(1)
        except Exception as e:  # noqa: BLE001 - any failure should just report, not crash the GUI
            self.line.emit(f"Error: {e}")
            self.done.emit(1)


class CmdThread(QThread):
    """Runs a sequence of (label, argv) steps, streaming output; stops at the first failure."""
    line = Signal(str)
    done = Signal(int)

    def __init__(self, steps):
        super().__init__()
        self.steps = steps

    def run(self):
        for label, argv in self.steps:
            self.line.emit(f"== {label} ==")
            try:
                p = subprocess.Popen(
                    argv, cwd=str(AGENT_DIR),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
                for ln in p.stdout:
                    self.line.emit(ln.rstrip())
                p.wait()
                if p.returncode != 0:
                    self.done.emit(p.returncode)
                    return
            except FileNotFoundError as e:
                self.line.emit(f"Not found: {e}. Is it installed and on PATH?")
                self.done.emit(1)
                return
            except Exception as e:  # noqa: BLE001
                self.line.emit(f"Error: {e}")
                self.done.emit(1)
                return
        self.done.emit(0)


class DownloadThread(QThread):
    """Download a file (e.g. the Ollama installer) to disk, reporting progress; empty path = failed."""
    line = Signal(str)
    done = Signal(str)

    def __init__(self, url, dest, label="Ollama"):
        super().__init__()
        self.url = url
        self.dest = dest
        self.label = label

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "Golem-Builder"})
            with urllib.request.urlopen(req, timeout=60) as r, open(self.dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    buf = r.read(262144)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    mb = got / 1_048_576
                    if total:
                        self.line.emit(f"Downloading {self.label}... {got * 100 // total}%  ({mb:.0f} MB)")
                    else:
                        self.line.emit(f"Downloading {self.label}... {mb:.0f} MB")
            self.done.emit(str(self.dest))
        except Exception as e:  # noqa: BLE001 - report, don't crash the GUI
            self.line.emit(f"Download failed: {e}")
            self.done.emit("")


def _parse_ver(tag: str) -> tuple:
    """Turn a tag like 'v0.5.1' into (0, 5, 1) for comparison; missing parts count as 0."""
    nums = re.findall(r"\d+", tag or "")[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums))


class UpdateCheckThread(QThread):
    """Ask GitHub (read-only) whether a newer release exists. Emits a dict; never raises into the GUI.
    It only *checks* - downloading and running an update is a separate step the user clicks."""
    result = Signal(dict)

    def run(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "Golem-Builder",
                                                       "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                rel = json.loads(r.read().decode("utf-8"))
            tag = rel.get("tag_name", "")
            # Match the installer by pattern, not an exact name, so a versioned asset like
            # "Golem-setup-0.7.1.exe" is found too (not just the legacy "Golem-setup.exe").
            def _is_installer(a):
                n = (a.get("name") or "").lower()
                return n.startswith("golem-setup") and n.endswith(".exe")
            assets = rel.get("assets", [])
            installer = next((a for a in assets if _is_installer(a)), None)
            self.result.emit({
                "ok": True,
                "tag": tag,
                "newer": _parse_ver(tag) > _parse_ver(VERSION),
                "url": rel.get("html_url", ""),
                "notes": (rel.get("body") or "").strip(),
                "asset_url": (installer or {}).get("browser_download_url", ""),
                "asset_size": (installer or {}).get("size", 0),
            })
        except Exception as e:  # noqa: BLE001 - offline / rate-limited / no releases: just report it
            self.result.emit({"ok": False, "error": str(e)})


class WhisperSetupThread(QThread):
    """Install the self-contained whisper.cpp speech-to-text engine + model (NO Python) - off-thread."""
    line = Signal(str)
    done = Signal(bool)

    def run(self):
        if not _agent_mod:
            self.line.emit("Speech-to-text setup isn't available in this build.")
            self.done.emit(False)
            return
        try:
            if _agent_mod.ensure_whisper(progress=self.line.emit):
                self.line.emit("Speech-to-text is ready. Click the microphone in the chat window to talk.")
                self.done.emit(True)
            else:
                self.line.emit("Couldn't download the speech-to-text engine (no internet?). Try again later.")
                self.done.emit(False)
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"Speech-to-text setup failed: {e}")
            self.done.emit(False)


class VoiceSetupThread(QThread):
    """Install the self-contained Piper engine + a voice (NO Python), then play a test line - off-thread.
    This is the one-click, works-on-a-clean-machine path the Add-ons Voice toggle uses."""
    line = Signal(str)
    done = Signal(bool)

    def __init__(self, voice: str, test: bool = True):
        super().__init__()
        self.voice = (voice or "").strip()
        self.test = test

    def run(self):
        if not _agent_mod:
            self.line.emit("Voice setup isn't available in this build.")
            self.done.emit(False)
            return
        try:
            if not _agent_mod.ensure_piper(progress=self.line.emit):
                self.line.emit("Couldn't download the voice engine (no internet?). Try again later.")
                self.done.emit(False)
                return
            if not _agent_mod.ensure_piper_voice(self.voice, progress=self.line.emit):
                self.line.emit("Couldn't download that voice - check the name on the Piper samples page.")
                self.done.emit(False)
                return
            if self.test:
                self.line.emit("Playing a test line...")
                import tempfile
                wav = Path(tempfile.gettempdir()) / "golem-voicetest.wav"
                ok, err = _agent_mod.piper_say("Hello, this is your Golem voice.", wav, self.voice)
                if ok and wav.exists() and os.name == "nt":
                    import winsound
                    winsound.PlaySound(str(wav), winsound.SND_FILENAME)
                try:
                    wav.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                if not ok:
                    self.line.emit(f"Voice installed, but the test line failed: {err}")
                    self.done.emit(True)
                    return
            self.line.emit(f"Voice '{_agent_mod.safe_voice(self.voice)}' is ready. Tick the box and Save to use it.")
            self.done.emit(True)
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"Voice setup failed: {e}")
            self.done.emit(False)


class Builder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Golem Builder {VERSION}")
        self.resize(720, 760)
        _ico = AGENT_DIR / "icon.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self.pull_thread = None
        self.addon_thread = None
        self.dl_thread = None  # Ollama installer download
        self.update_thread = None  # GitHub release check
        self.update_dl = None      # downloading the new installer
        self.update_info = None    # the latest release info once checked
        self._easy_pending_launch = False  # Easy Mode: launch the agent once the model finishes pulling
        self._easy_pending_ollama = False  # Easy Mode: continue once Ollama finishes installing
        self.voice_test_thread = None      # running voice download/test
        self.stt_thread = None             # running speech-to-text install
        self._routines = []                # saved quick-task routines
        self._rec = None  # the latest GPU model recommendation
        self.cfg = self.load_config()

        root = QVBoxLayout(self)
        title = QLabel("Golem Builder")
        title.setFont(QFont("", 18, QFont.Bold))
        root.addWidget(title)
        root.addWidget(QLabel("Build a custom AI agent for this PC. Everything below is optional."))
        root.addWidget(self._update_bar())

        # New here? One button sets up a safe, friendly agent with nothing to configure.
        easy_btn = QPushButton("⚡  Easy Mode  —  one-click setup (recommended for beginners)")
        easy_btn.setCursor(Qt.PointingHandCursor)
        easy_btn.setStyleSheet("font-weight:bold; padding:11px; background:#1f6feb; color:white; "
                               "border:none; border-radius:9px; font-size:14px;")
        easy_btn.clicked.connect(self.easy_mode)
        root.addWidget(easy_btn)
        easy_hint = QLabel("Sets up a safe, friendly assistant fenced to its own folder, picks the right "
                           "free brain for your PC, and launches it. You can ignore the tabs below.")
        easy_hint.setStyleSheet("color:#888;")
        easy_hint.setWordWrap(True)
        root.addWidget(easy_hint)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._model_tab(), "Brain")
        self.tabs.addTab(self._features_tab(), "Features")
        self.tabs.addTab(self._folders_tab(), "Folders")
        # Profile goes first (one-click setup) but is built last, so the boxes it drives already exist.
        self.tabs.insertTab(0, self._profile_tab(), "Profile")
        self.tabs.setCurrentIndex(0)
        self.tabs.addTab(self._personality_tab(), "Personality")
        self.tabs.addTab(self._phone_tab(), "Phone")
        self.tabs.addTab(self._addons_tab(), "Add-ons")
        root.addWidget(self.tabs, 1)

        # Bottom action bar
        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: #2a7;")
        bar.addWidget(self.status, 1)
        for text, slot in [("Save", self.save), ("Save & Launch agent", self.launch_agent),
                           ("Save & Launch phone server", self.launch_server)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        root.addLayout(bar)

        self.load_into_widgets()
        self.detect_gpu_clicked()      # show the GPU + a recommendation on startup
        self.refresh_ollama_status()
        if self._fresh:                # first run: apply the GPU recommendation automatically
            self.apply_recommendation()
        if self.cfg.get("check_updates", True):  # quietly see if a newer release is out (read-only)
            self.check_for_updates(manual=False)
        if self.cfg.get("first_run", True):       # a warm welcome the very first time, pointing at Easy Mode
            QTimer.singleShot(400, self._welcome)

    def _welcome(self):
        QMessageBox.information(
            self, "Welcome to Golem",
            "Golem is your own AI that runs right here on your computer - free, private, nothing sent "
            "anywhere.\n\n"
            "If you're new to this, just click the blue “Easy Mode” button at the top. Golem will set "
            "everything up for you - no terminal, no accounts - and then open a chat window where you can "
            "talk or type.\n\n"
            "That's it. Click Easy Mode and you're off.")

    # ---------------------------------------------------------------- config I/O

    def load_config(self) -> dict:
        self._fresh = not CONFIG_FILE.exists()  # first run? then auto-apply the GPU recommendation
        try:
            sys.path.insert(0, str(AGENT_DIR))
            import agent  # reuse the single source of truth for the default shape
            base = agent.default_config()
        except Exception:
            base = {"name": "Golem",
                    "model": {"mode": "auto", "local_model": "gemma4:12b",
                              "text_model": "hermes3", "vision_model": "gemma4:12b", "ctx": 65536},
                    "features": {k: True for k, _, _ in FEATURES},
                    "serve": {"enabled": False, "host": "0.0.0.0", "port": 8765, "token": ""},
                    "personality": ""}
        if CONFIG_FILE.exists():
            try:
                saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                base.update(saved)
            except Exception:
                pass
        return base

    def collect(self) -> dict:
        mode = "auto" if self.r_auto.isChecked() else ("local" if self.r_local.isChecked() else "claude")
        return {
            "name": self.name_edit.text().strip() or "Golem",
            "profile": self.profile_combo.currentData() or "general",
            "model": {
                "mode": mode,
                "local_model": self.local_combo.currentText().strip(),
                "text_model": self.text_combo.currentText().strip(),
                "vision_model": self.vision_combo.currentText().strip(),
                "ctx": self.ctx_spin.value(),
            },
            "features": {key: cb.isChecked() for key, cb in self.feature_boxes.items()},
            "sandbox": {
                "enabled": self.sandbox_check.isChecked() if getattr(self, "sandbox_check", None) else False,
                "backend": "auto",
                "network": (self.network_combo.currentData() if getattr(self, "network_combo", None) else "off"),
            },
            "serve": {
                "enabled": self.serve_check.isChecked(),
                "host": "0.0.0.0",
                "port": self.port_spin.value(),
                "token": self.token_edit.text().strip(),
            },
            "folders": [self.folders_list.item(i).text() for i in range(self.folders_list.count())],
            "sticky_taint": self.sticky_check.isChecked(),
            "hardened": self.hardened_check.isChecked(),
            "check_updates": self.update_check_box.isChecked(),
            "ollama_install_dir": self.ollama_install_edit.text().strip() if getattr(self, "ollama_install_edit", None) else "",
            "ollama_models_dir": self.ollama_models_edit.text().strip() if getattr(self, "ollama_models_edit", None) else "",
            "voice": {
                "enabled": self.voice_check.isChecked() if getattr(self, "voice_check", None) else False,
                "voice": (self.voice_combo.currentText().strip() if getattr(self, "voice_combo", None) else "")
                         or "en_US-hfc_female-medium",
                "wake": self.stt_wake_check.isChecked() if getattr(self, "stt_wake_check", None) else False,
            },
            "orb_autostart": self.orb_autostart_check.isChecked() if getattr(self, "orb_autostart_check", None) else False,
            "routines": list(self._routines),
            "home_assistant": {
                "url": self.ha_url_edit.text().strip() if getattr(self, "ha_url_edit", None) else "",
                "token": self.ha_token_edit.text().strip() if getattr(self, "ha_token_edit", None) else "",
            },
            "tray_presence": self.tray_presence_check.isChecked() if getattr(self, "tray_presence_check", None) else False,
            "proactive": {
                "enabled": self.proactive_check.isChecked() if getattr(self, "proactive_check", None) else False,
                "interval_min": self.proactive_interval.value() if getattr(self, "proactive_interval", None) else 15,
            },
            "personality": self.persona_edit.toPlainText().strip(),
        }

    def save(self):
        # Preserve keys the Builder doesn't manage (e.g. the agent window's "appearance") instead of
        # dropping them: start from the existing file and let the collected settings win.
        merged = {}
        try:
            merged = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            merged = {}
        merged.update(self.collect())
        self.cfg = merged
        # Hardened Mode confines reads/writes to the allowed folders - with none set, there's nothing to
        # confine to, so point that out (once per save, not a hard block).
        if self.cfg.get("hardened") and not self.cfg.get("folders"):
            QMessageBox.information(
                self, "Hardened Mode - add a folder",
                "Hardened Mode is on but you haven't set any allowed folders, so there's nothing to confine "
                "the agent to - reads and writes stay wide open.\n\nAdd at least one folder on the Folders "
                "tab to get the full benefit. (The other Hardened protections - sticky re-confirm and "
                "PC-only phone access - still apply.)")
        CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        self.flash(f"Saved to {CONFIG_FILE.name}")

    def flash(self, msg: str):
        self.status.setText(msg)

    def closeEvent(self, event):
        # Hard-exit so Golem-Builder.exe never lingers in the background with its worker threads still
        # running (the agent window and orb it launched are separate processes and keep running).
        event.accept()
        os._exit(0)

    # ---------------------------------------------------------------- auto-update
    #
    # Checking is automatic and read-only (a GitHub API call on launch). Downloading and RUNNING the new
    # installer only happens when the user clicks "Update now" - Golem never silently fetches and executes
    # code from the internet. The installer upgrades in place (same per-user folder, no admin, no reinstall).

    def _update_bar(self) -> QWidget:
        self.update_bar = QFrame()
        # Scope the background to the frame itself (#id) so it doesn't cascade onto the child buttons -
        # that cascade was making the buttons dark green with unreadable near-black text.
        self.update_bar.setObjectName("updateBar")
        self.update_bar.setStyleSheet(
            "#updateBar { background:#1f7a3f; border:1px solid #39a35a; border-radius:10px; }")
        lay = QHBoxLayout(self.update_bar)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        self.update_label = QLabel("")
        self.update_label.setStyleSheet("color:#ffffff; font-size:15px; font-weight:700; "
                                        "background:transparent; border:none;")
        self.update_label.setWordWrap(True)
        lay.addWidget(self.update_label, 1)
        self.update_notes_btn = QPushButton("What's new")
        self.update_notes_btn.clicked.connect(self._show_update_notes)
        self.update_now_btn = QPushButton("Update now")
        self.update_now_btn.clicked.connect(self.do_update)
        self.update_skip_btn = QPushButton("Skip")
        self.update_skip_btn.clicked.connect(lambda: self.update_bar.setVisible(False))
        # The primary action stands out (white on bright green); the others are readable light-on-dark.
        self.update_now_btn.setStyleSheet(
            "background:#ffffff; color:#176b35; font-weight:700; font-size:14px; border:none; "
            "border-radius:8px; padding:9px 20px;")
        for b in (self.update_notes_btn, self.update_skip_btn):
            b.setStyleSheet("background:#15592e; color:#ffffff; font-size:14px; border:1px solid #39a35a; "
                            "border-radius:8px; padding:9px 16px;")
        for b in (self.update_notes_btn, self.update_now_btn, self.update_skip_btn):
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)
        self.update_bar.setVisible(False)  # hidden until a newer release is found
        return self.update_bar

    def check_for_updates(self, manual: bool = False):
        if self.update_thread and self.update_thread.isRunning():
            return
        self._manual_check = manual
        if manual:
            self.flash("Checking for updates...")
        self.update_thread = UpdateCheckThread()
        self.update_thread.result.connect(self._on_update_result)
        self.update_thread.start()

    def _on_update_result(self, info: dict):
        if not info.get("ok"):
            if getattr(self, "_manual_check", False):
                self.flash(f"Update check failed: {info.get('error', 'unknown error')}")
            return
        self.update_info = info
        if info.get("newer") and info.get("asset_url"):
            self.update_label.setText(f"Golem {info['tag']} is available  (you have {VERSION}).")
            self.update_bar.setVisible(True)
            if getattr(self, "_manual_check", False):
                self.flash("")
        elif info.get("newer") and not info.get("asset_url"):
            # A newer tag exists but no installer asset yet (release still being published)
            if getattr(self, "_manual_check", False):
                self.flash(f"Golem {info['tag']} is listed but its installer isn't uploaded yet.")
        elif getattr(self, "_manual_check", False):
            self.flash(f"You're up to date (Golem {VERSION}).")

    def _show_update_notes(self):
        info = self.update_info or {}
        notes = info.get("notes") or "(no release notes)"
        box = QMessageBox(self)
        box.setWindowTitle(f"What's new in Golem {info.get('tag', '')}")
        box.setText(notes[:4000])
        open_btn = box.addButton("Open release page", QMessageBox.ActionRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and info.get("url"):
            webbrowser.open(info["url"])

    def do_update(self):
        info = self.update_info or {}
        url = info.get("asset_url")
        if not url:
            QMessageBox.warning(self, "No installer", "Couldn't find the installer for the new release.")
            return
        mb = round((info.get("asset_size") or 0) / 1_048_576)
        ok = QMessageBox.question(
            self, "Download and install the update?",
            f"This will download Golem {info.get('tag', '')} (about {mb} MB) from GitHub and run the "
            f"installer, which upgrades your current copy in place (no admin needed).\n\nGolem will close "
            f"during the install. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ok != QMessageBox.Yes:
            return
        dest = Path(os.environ.get("TEMP", str(AGENT_DIR))) / "Golem-setup.exe"
        self.update_now_btn.setEnabled(False)
        self.update_dl = DownloadThread(url, str(dest), label=f"Golem {info.get('tag', '')}")
        self.update_dl.line.connect(self.flash)
        self.update_dl.done.connect(self._on_update_downloaded)
        self.update_dl.start()

    def _on_update_downloaded(self, path: str):
        self.update_now_btn.setEnabled(True)
        if not path or not Path(path).exists() or Path(path).stat().st_size < 1_000_000:
            QMessageBox.critical(self, "Download failed",
                                 "The update didn't download completely. Check your internet and try again.")
            return
        try:
            # Close the other Golem windows first so none of their exes stay locked during the install
            # (a locked exe is what made the update fail with a "file in use" error).
            if os.name == "nt":
                for exe in ("Golem.exe", "Golem-Orb.exe", "golem-core.exe"):
                    try:
                        subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    except Exception:  # noqa: BLE001
                        pass
            # Launch the installer, then CLOSE the Builder too so the installer can replace its files.
            # os.startfile launches it as an independent process, so it keeps running after we quit. The
            # installer reopens the Builder on its Finish page.
            os.startfile(path)
            self.flash("Updating Golem - it will close now so the update can install, then reopen.")
            QTimer.singleShot(1500, QApplication.quit)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not start the installer",
                                 f"Downloaded to {path}, but couldn't run it:\n{e}")

    # ---------------------------------------------------------------- Brain tab

    def _model_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        orow = QHBoxLayout()
        self.ollama_label = QLabel("Checking Ollama...")
        self.ollama_label.setWordWrap(True)
        self.install_ollama_btn = QPushButton("Install for me")
        self.install_ollama_btn.setToolTip("Download and run the official Ollama installer from ollama.com")
        self.install_ollama_btn.clicked.connect(self.install_ollama)
        get_ollama = QPushButton("Get Ollama")
        get_ollama.clicked.connect(lambda: webbrowser.open("https://ollama.com/download"))
        orow.addWidget(self.ollama_label, 1)
        orow.addWidget(self.install_ollama_btn)
        orow.addWidget(get_ollama)
        lay.addLayout(orow)

        gpu_box = QGroupBox("Your GPU")
        gl = QVBoxLayout(gpu_box)
        self.gpu_label = QLabel("Detecting your graphics card...")
        self.gpu_label.setWordWrap(True)
        grow = QHBoxLayout()
        detect_btn = QPushButton("Detect my GPU")
        detect_btn.clicked.connect(self.detect_gpu_clicked)
        self.apply_rec_btn = QPushButton("Use recommended")
        self.apply_rec_btn.clicked.connect(self.apply_recommendation)
        self.apply_rec_btn.setEnabled(False)
        grow.addWidget(detect_btn)
        grow.addWidget(self.apply_rec_btn)
        grow.addStretch(1)
        gl.addWidget(self.gpu_label)
        gl.addLayout(grow)
        lay.addWidget(gpu_box)

        box = QGroupBox("How should the agent think?")
        g = QVBoxLayout(box)
        self.r_auto = QRadioButton("Auto - switch brains per task (recommended)")
        self.r_local = QRadioButton("One local model")
        self.r_claude = QRadioButton("Claude API (needs an API key, costs tokens)")
        self.mode_group = QButtonGroup(self)
        for r in (self.r_auto, self.r_local, self.r_claude):
            self.mode_group.addButton(r)
            g.addWidget(r)
        for r in (self.r_auto, self.r_local, self.r_claude):
            r.toggled.connect(self._refresh_model_fields)
        lay.addWidget(box)

        form = QFormLayout()
        installed = ollama_models()
        self.text_combo = self._model_combo(installed)
        self.vision_combo = self._model_combo(installed)
        self.local_combo = self._model_combo(installed)
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(2048, 131072)
        self.ctx_spin.setSingleStep(4096)
        self.ctx_spin.setValue(65536)
        self.auto_note = QLabel("Auto uses a fast, uncensored text model for most work and the vision model "
                                "only when it needs to see the screen or an image.")
        self.auto_note.setWordWrap(True)
        form.addRow("Text brain (work):", self.text_combo)
        form.addRow("Vision brain (sees):", self.vision_combo)
        form.addRow("Single local model:", self.local_combo)
        form.addRow("Context window:", self.ctx_spin)
        lay.addLayout(form)
        lay.addWidget(self.auto_note)

        # Pull a new model
        pull_box = QGroupBox("Install a model with Ollama")
        pg = QGridLayout(pull_box)
        self.pull_combo = QComboBox()
        self.pull_combo.setEditable(True)
        self.pull_combo.addItems(SUGGESTED_MODELS)
        self.pull_btn = QPushButton("Pull")
        self.pull_btn.clicked.connect(self.pull_model)
        self.refresh_btn = QPushButton("Refresh list")
        self.refresh_btn.clicked.connect(self.refresh_models)
        pg.addWidget(QLabel("Model tag:"), 0, 0)
        pg.addWidget(self.pull_combo, 0, 1)
        pg.addWidget(self.pull_btn, 0, 2)
        pg.addWidget(self.refresh_btn, 0, 3)
        self.pull_log = QLabel("")
        self.pull_log.setStyleSheet("color: #888;")
        pg.addWidget(self.pull_log, 1, 0, 1, 4)
        browse_ollama = QPushButton("Browse Ollama library")
        browse_ollama.clicked.connect(lambda: webbrowser.open("https://ollama.com/library"))
        browse_hf = QPushButton("Browse Hugging Face (GGUF)")
        browse_hf.clicked.connect(lambda: webbrowser.open("https://huggingface.co/models?library=gguf&sort=trending"))
        pg.addWidget(browse_ollama, 2, 0, 1, 2)
        pg.addWidget(browse_hf, 2, 2, 1, 2)
        hint = QLabel("Thousands of models: pull any name from Ollama's library, or any GGUF from Hugging Face "
                      "by pulling  hf.co/USER/REPO  (add :Q4_K_M for a smaller quant). Paste a tag above and Pull.")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        pg.addWidget(hint, 3, 0, 1, 4)
        lay.addWidget(pull_box)

        # Where Ollama installs and (more importantly) where it keeps the big model files. By default
        # both land on C:; this lets the user move them - especially the models, which can be tens of GB.
        loc_box = QGroupBox("Where Ollama installs && stores models (optional - defaults to C:)")
        lg = QGridLayout(loc_box)
        self.ollama_install_edit = QLineEdit()
        self.ollama_install_edit.setReadOnly(True)
        self.ollama_install_edit.setPlaceholderText("Default (your home folder on C:)")
        inst_btn = QPushButton("Choose...")
        inst_btn.clicked.connect(self.choose_install_dir)
        lg.addWidget(QLabel("Install Ollama to:"), 0, 0)
        lg.addWidget(self.ollama_install_edit, 0, 1)
        lg.addWidget(inst_btn, 0, 2)

        self.ollama_models_edit = QLineEdit()
        self.ollama_models_edit.setReadOnly(True)
        self.ollama_models_edit.setPlaceholderText("Default (C:\\Users\\you\\.ollama\\models)")
        mod_btn = QPushButton("Choose...")
        mod_btn.clicked.connect(self.choose_models_dir)
        apply_mod_btn = QPushButton("Apply models folder")
        apply_mod_btn.clicked.connect(self.apply_models_dir)
        lg.addWidget(QLabel("Keep models in:"), 1, 0)
        lg.addWidget(self.ollama_models_edit, 1, 1)
        lg.addWidget(mod_btn, 1, 2)
        lg.addWidget(apply_mod_btn, 2, 1, 1, 2)

        loc_note = QLabel("Models are huge (tens of GB). Pick another drive here to keep them off C:. "
                          "'Install to' only applies when you install Ollama with the button below. "
                          "'Keep models in' takes effect after you click Apply and restart Ollama "
                          "(quit it from the system tray, then reopen it).")
        loc_note.setStyleSheet("color:#888;")
        loc_note.setWordWrap(True)
        lg.addWidget(loc_note, 3, 0, 1, 3)
        lay.addWidget(loc_box)

        if not installed:
            note = QLabel("No Ollama models found (or Ollama isn't running). Install one above, then Refresh.")
            note.setStyleSheet("color: #c60;")
            note.setWordWrap(True)
            lay.addWidget(note)

        lay.addStretch(1)
        return w

    def _model_combo(self, installed) -> QComboBox:
        c = QComboBox()
        c.setEditable(True)
        c.addItems(installed)
        return c

    def _refresh_model_fields(self):
        auto = self.r_auto.isChecked()
        local = self.r_local.isChecked()
        self.text_combo.setEnabled(auto)
        self.vision_combo.setEnabled(auto)
        self.auto_note.setVisible(auto)
        self.local_combo.setEnabled(local)
        self.ctx_spin.setEnabled(not self.r_claude.isChecked())

    def refresh_models(self):
        installed = ollama_models()
        for combo in (self.text_combo, self.vision_combo, self.local_combo):
            current = combo.currentText()
            combo.clear()
            combo.addItems(installed)
            combo.setCurrentText(current)
        self.refresh_ollama_status()
        self.flash(f"Found {len(installed)} installed model(s).")

    def pull_model(self):
        model = self.pull_combo.currentText().strip()
        if not model or self.pull_thread:
            return
        self.pull_btn.setEnabled(False)
        self.pull_log.setText(f"Pulling {model} ...")
        self.pull_thread = PullThread(model)
        self.pull_thread.line.connect(lambda ln: self.pull_log.setText(ln))
        self.pull_thread.done.connect(self._pull_done)
        self.pull_thread.start()

    def _pull_done(self, code):
        self.pull_btn.setEnabled(True)
        self.pull_thread = None
        self.pull_log.setText("Done." if code == 0 else "Pull failed - see the message above.")
        if code == 0:
            self.refresh_models()
            if self._easy_pending_launch:  # Easy Mode was waiting on this model to finish downloading
                self._easy_pending_launch = False
                self.launch_agent()
                self.flash("Easy Mode: the brain is ready - launched Golem in its own window.")

    def detect_gpu_clicked(self):
        name, vram = detect_gpu()
        self._rec = recommend_for(vram)
        where = name or "No GPU detected"
        vtxt = f" - {vram} GB" if vram else ""
        pick = (f"Auto ({self._rec['text']} + {self._rec['vision']})" if self._rec["mode"] == "auto"
                else self._rec["local"])
        self.gpu_label.setText(f"{where}{vtxt}\nRecommended: {pick}\n{self._rec['note']}")
        self.apply_rec_btn.setEnabled(True)

    def apply_recommendation(self):
        if not self._rec:
            return
        r = self._rec
        self.r_auto.setChecked(r["mode"] == "auto")
        self.r_local.setChecked(r["mode"] == "local")
        if r["text"]:
            self.text_combo.setCurrentText(r["text"])
        if r["vision"]:
            self.vision_combo.setCurrentText(r["vision"])
        if r["local"]:
            self.local_combo.setCurrentText(r["local"])
        self._refresh_model_fields()
        self.flash("Applied the recommended model for your GPU.")

    def refresh_ollama_status(self):
        running = ollama_running()
        if running:
            n = len(ollama_models())
            self.ollama_label.setText(f"Ollama: running ✓  ({n} model{'' if n == 1 else 's'} installed)")
            self.ollama_label.setStyleSheet("color:#2a7;")
        else:
            self.ollama_label.setText("Ollama: not found or not running. Click 'Install for me' to set it up "
                                      "automatically, then Refresh list.")
            self.ollama_label.setStyleSheet("color:#c60;")
        # The one-click installer is Windows-only; elsewhere the Get Ollama link still works.
        if hasattr(self, "install_ollama_btn"):
            self.install_ollama_btn.setVisible(os.name == "nt" and not running and not self.dl_thread)

    def choose_install_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to install Ollama")
        if d:
            self.ollama_install_edit.setText(d)

    def choose_models_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to keep the (large) model files")
        if d:
            self.ollama_models_edit.setText(d)

    def apply_models_dir(self):
        """Point Ollama at a new models folder by setting the OLLAMA_MODELS user environment variable
        (the supported way on Windows). Ollama picks it up next time its server starts."""
        path = self.ollama_models_edit.text().strip()
        if not path:
            QMessageBox.information(self, "Models folder",
                                    "No folder chosen, so Ollama keeps using the default location on C:. "
                                    "Click 'Choose...' first to pick another drive.")
            return
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Couldn't use that folder", f"{path}\n\n{e}")
            return
        os.environ["OLLAMA_MODELS"] = path  # this process (and anything it launches) right away
        ok = True
        if os.name == "nt":
            try:  # persist it as a user environment variable so Ollama sees it after a restart
                subprocess.run(["setx", "OLLAMA_MODELS", path], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:  # noqa: BLE001
                ok = False
        self.save()  # remember it in the config too
        msg = (f"Ollama will keep models in:\n{path}\n\n"
               "To use it now, quit Ollama from the system tray and reopen it from the Start menu "
               "(or restart your PC). Models you already downloaded stay where they are - move them into "
               "the new folder if you want Ollama to see them there.")
        if not ok:
            msg += "\n\n(Note: I couldn't set it permanently - set OLLAMA_MODELS yourself in Windows " \
                   "environment variables if it doesn't stick.)"
        QMessageBox.information(self, "Models folder set", msg)
        self.flash(f"Models folder set to {path} - restart Ollama to apply.")

    OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"

    def install_ollama(self):
        """Download OllamaSetup.exe and install it SILENTLY - the user never sees Ollama's own window."""
        if self.dl_thread:
            return
        ok = QMessageBox.question(
            self, "Install Ollama",
            "This downloads Ollama - the free local AI engine - (~700 MB) and installs it for you in the "
            "background. There's nothing for you to click.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        dest = Path(os.environ.get("TEMP", str(AGENT_DIR))) / "OllamaSetup.exe"
        self.install_ollama_btn.setEnabled(False)
        self.ollama_label.setText("Downloading Ollama...")
        self.ollama_label.setStyleSheet("color:#2d6cdf;")
        self.dl_thread = DownloadThread(self.OLLAMA_SETUP_URL, dest)
        self.dl_thread.line.connect(lambda ln: self.ollama_label.setText(ln))
        self.dl_thread.done.connect(self._ollama_downloaded)
        self.dl_thread.start()

    def _finish_ollama_setup(self):
        """Keep Ollama local-only (no account) and close its desktop window; Golem drives the server."""
        try:
            if _agent_mod:
                _agent_mod.ollama_stay_local()
                _agent_mod.close_ollama_window()
        except Exception:  # noqa: BLE001
            pass

    def _ollama_downloaded(self, path: str):
        self.dl_thread = None
        if not path:
            self.install_ollama_btn.setEnabled(True)
            self.ollama_label.setText("Couldn't download Ollama. Check your internet and try again.")
            self.ollama_label.setStyleSheet("color:#c60;")
            return
        install_dir = self.ollama_install_edit.text().strip() if hasattr(self, "ollama_install_edit") else ""
        args = [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        if install_dir:
            args.append(f"/DIR={install_dir}")
        self.ollama_label.setText("Installing Ollama in the background (about a minute)...")
        self.ollama_label.setStyleSheet("color:#2d6cdf;")
        try:
            subprocess.Popen(args, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            # poll until the server comes up, then lock it to local-only and close its window
            self._ollama_wait = 60
            self._ollama_poll = QTimer(self)
            self._ollama_poll.timeout.connect(self._poll_ollama_install)
            self._ollama_poll.start(2000)
        except Exception as e:  # noqa: BLE001
            self.install_ollama_btn.setEnabled(True)
            self.ollama_label.setText(f"Downloaded, but couldn't run the installer ({e}). It's at {path}.")
            self.ollama_label.setStyleSheet("color:#c60;")

    def _poll_ollama_install(self):
        self._ollama_wait -= 2
        if ollama_running() or self._ollama_wait <= 0:
            self._ollama_poll.stop()
            self.install_ollama_btn.setEnabled(True)
            self._finish_ollama_setup()   # local-only + close the Ollama window
            if ollama_running():
                self.ollama_label.setText("Ollama is installed and running locally. ✓")
                self.ollama_label.setStyleSheet("color:#2a7;")
                self.refresh_models()
                if getattr(self, "_easy_pending_ollama", False):
                    self._easy_pending_ollama = False
                    self.easy_mode()   # beginner flow: carry on now that Ollama is ready
            else:
                self.ollama_label.setText("Ollama was installed - click 'Refresh list' in a moment.")
                self.ollama_label.setStyleSheet("color:#c60;")

    # ---------------------------------------------------------------- Features tab

    # ---------------------------------------------------------------- Profile tab (0.6.0)

    def _profile_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Set this agent up for a job in one click. A profile fills the other tabs "
                             "(features, folders, protections, voice) - you can still tweak anything "
                             "before you save."))
        self.profile_combo = QComboBox()
        for pid, p in PROFILES.items():
            self.profile_combo.addItem(p.get("label", pid), pid)
        lay.addWidget(self.profile_combo)

        self.profile_desc = QLabel("")
        self.profile_desc.setWordWrap(True)
        self.profile_desc.setStyleSheet("color:#888;")
        lay.addWidget(self.profile_desc)

        apply_btn = QPushButton("Apply this profile")
        apply_btn.clicked.connect(self.apply_profile_clicked)
        lay.addWidget(apply_btn)

        note = QLabel("Profiles carry their own guardrails too - e.g. the Shopping agent never buys "
                      "without your say-so, the Medical one keeps patient data on this PC, the Student "
                      "one tutors instead of doing the homework. Those stay on whatever voice you pick.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        lay.addWidget(note)

        self.profile_combo.currentIndexChanged.connect(self._profile_desc_update)
        self._profile_desc_update()
        lay.addStretch(1)
        return w

    def _profile_desc_update(self):
        pid = self.profile_combo.currentData()
        self.profile_desc.setText(PROFILES.get(pid, {}).get("description", ""))

    def apply_profile_clicked(self):
        self.apply_profile(self.profile_combo.currentData() or "general")

    def _select_tab(self, name: str):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

    def easy_mode(self):
        """One click for a complete beginner: a safe, friendly agent, the right free brain, and launch.
        As automatic as a local-first agent can be - the only thing the user may still have to click
        through is the Ollama installer itself (a Windows wizard we can't press for them)."""
        # 1. The safe beginner profile (friendly voice, fenced, no shell/screen).
        if "easy" in PROFILES:
            self.apply_profile("easy")
            idx = self.profile_combo.findData("easy")
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        # 2. Let Golem pick the free brain that fits this PC.
        self.r_auto.setChecked(True)
        if self._rec:
            self.apply_recommendation()
        if getattr(self, "orb_autostart_check", None) is not None:
            self.orb_autostart_check.setChecked(True)   # the ambient orb is standard in Easy Mode
        # 3. A folder of its own, created for them, so Hardened Mode has something to fence to.
        folder = Path.home() / "Documents" / "Golem"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        existing = [self.folders_list.item(i).text() for i in range(self.folders_list.count())]
        if str(folder) not in existing:
            self.folders_list.addItem(str(folder))
        if not self.name_edit.text().strip():
            self.name_edit.setText("Golem")
        # 4. Save it all.
        self.save()
        # 5. Make sure the brain is actually there, then launch.
        self._finish_ollama_setup()   # keep Ollama local-only and close its window if it's around
        if not ollama_running():
            ok = QMessageBox.question(
                self, "Easy Mode - set up the free brain (one time)",
                "Golem needs its free local brain (Ollama), and it isn't set up yet.\n\n"
                "I'll download and install it for you in the background (about 1 GB, nothing to click), "
                "then finish setting up and launch Golem on its own. Do that now?",
                QMessageBox.Yes | QMessageBox.No)
            if ok == QMessageBox.Yes and os.name == "nt":
                self._easy_pending_ollama = True   # the install poller re-runs Easy Mode when Ollama is up
                self._select_tab("Brain")
                self.install_ollama()
            return
        if not ollama_models():
            model = (self._rec or {}).get("text") or (self._rec or {}).get("local") or "llama3.2:3b"
            QMessageBox.information(
                self, "Easy Mode - downloading the brain",
                f"Getting the '{model}' model now - it's a few GB and only happens once. "
                "Golem will open on its own when it's ready; you can watch progress on the Brain tab.")
            self._easy_pending_launch = True
            self._select_tab("Brain")
            self.pull_combo.setCurrentText(model)
            self.pull_model()
            return
        self.launch_agent()
        self.flash("Easy Mode: launched a safe, friendly Golem in its own window.")

    def launch_orb(self):
        exe = AGENT_DIR / "Golem-Orb.exe"
        try:
            if exe.exists():
                subprocess.Popen([str(exe)], cwd=str(AGENT_DIR))
            else:
                pyw = agent_python()
                if pyw.endswith("python.exe") and os.path.exists(pyw[:-10] + "pythonw.exe"):
                    pyw = pyw[:-10] + "pythonw.exe"
                subprocess.Popen([pyw, str(AGENT_DIR / "orb.py")], cwd=str(AGENT_DIR))
            self.flash("Launched the orb - look bottom-right; control it from the tray icon.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not launch the orb", str(e))

    def _voice_installed(self) -> bool:
        if _agent_mod:
            return _agent_mod.piper_installed()
        return (AGENT_DIR / "piper" / ("piper.exe" if os.name == "nt" else "piper")).exists()

    def setup_voice(self, test: bool = True):
        if getattr(self, "voice_test_thread", None):
            return
        v = self.voice_combo.currentText().strip() or "en_US-hfc_female-medium"
        self.voice_install_btn.setEnabled(False)
        self.voice_test_btn.setEnabled(False)
        self.voice_log.setText("Setting up the voice (one-time download)...")
        self.voice_test_thread = VoiceSetupThread(v, test=test)
        self.voice_test_thread.line.connect(self.voice_log.setText)
        self.voice_test_thread.done.connect(self._voice_setup_done)
        self.voice_test_thread.start()

    def _voice_setup_done(self, ok: bool):
        self.voice_install_btn.setEnabled(True)
        self.voice_test_btn.setEnabled(True)
        self.voice_test_thread = None
        self._refresh_addon_status()

    def _whisper_installed(self) -> bool:
        if _agent_mod:
            return _agent_mod.whisper_installed()
        return (AGENT_DIR / "whisper" / ("whisper-cli.exe" if os.name == "nt" else "whisper-cli")).exists()

    def setup_stt(self):
        if getattr(self, "stt_thread", None):
            return
        self.stt_btn.setEnabled(False)
        self.stt_log.setText("Setting up speech-to-text (one-time download)...")
        self.stt_thread = WhisperSetupThread()
        self.stt_thread.line.connect(self.stt_log.setText)
        self.stt_thread.done.connect(self._stt_setup_done)
        self.stt_thread.start()

    def _stt_setup_done(self, ok: bool):
        self.stt_btn.setEnabled(True)
        self.stt_thread = None
        self._refresh_addon_status()

    # ---------------------------------------------------------------- routines (quick tasks)

    def _refresh_routines_list(self):
        if getattr(self, "routines_list", None) is None:
            return
        self.routines_list.clear()
        for r in self._routines:
            tags = []
            if r.get("at_launch"):
                tags.append("⏻ at launch")
            if (r.get("at") or "").strip():
                tags.append(f"\U0001f553 {r['at']} daily")
            tag = ("   " + ", ".join(tags)) if tags else ""
            self.routines_list.addItem(f"{r.get('name', 'Task')}{tag}")

    def add_routine(self):
        name, ok = QInputDialog.getText(self, "New routine", "Button name (short):")
        if not ok or not name.strip():
            return
        prompt, ok = QInputDialog.getMultiLineText(
            self, "New routine", f"What should Golem do when you click “{name.strip()}”?")
        if not ok or not prompt.strip():
            return
        self._routines.append({"name": name.strip()[:24], "prompt": prompt.strip(), "at_launch": False})
        self._refresh_routines_list()

    def remove_routine(self):
        i = self.routines_list.currentRow()
        if 0 <= i < len(self._routines):
            del self._routines[i]
            self._refresh_routines_list()

    def toggle_routine_launch(self):
        i = self.routines_list.currentRow()
        if 0 <= i < len(self._routines):
            self._routines[i]["at_launch"] = not self._routines[i].get("at_launch", False)
            self._refresh_routines_list()

    def set_routine_time(self):
        i = self.routines_list.currentRow()
        if not (0 <= i < len(self._routines)):
            return
        cur = self._routines[i].get("at", "")
        txt, ok = QInputDialog.getText(self, "Daily time",
                                       "Run this routine every day at (24h HH:MM, blank to clear):", text=cur)
        if not ok:
            return
        txt = txt.strip()
        if txt and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", txt):
            QMessageBox.warning(self, "Bad time", "Please use 24-hour HH:MM, like 08:00 or 17:30.")
            return
        self._routines[i]["at"] = txt
        self._refresh_routines_list()

    def apply_profile(self, pid: str):
        prof = PROFILES.get(pid, {})
        feats = prof.get("features") or {}
        for key, cb in self.feature_boxes.items():
            if key in feats:
                cb.setChecked(bool(feats[key]))
        self.hardened_check.setChecked(bool(prof.get("hardened", False)))
        if prof.get("hardened"):
            self.sticky_check.setChecked(True)
        if getattr(self, "sandbox_check", None) is not None and self.sandbox_check.isEnabled():
            self.sandbox_check.setChecked(bool(prof.get("sandbox", False)))
        if prof.get("persona"):
            self.persona_edit.setPlainText(prof["persona"])
        self.flash(f"Applied the {prof.get('label', pid)} profile - review the tabs, then Save.")

    def _features_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Tick the abilities this agent should have. Each one only adds its own tools."))
        self.feature_boxes = {}
        for key, label, helptext in FEATURES:
            row = QFrame()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            cb = QCheckBox(label)
            cb.setFont(QFont("", 11, QFont.Bold))
            sub = QLabel(helptext)
            sub.setStyleSheet("color: #888;")
            sub.setWordWrap(True)
            rl.addWidget(cb)
            rl.addWidget(sub)
            lay.addWidget(row)
            self.feature_boxes[key] = cb
        lay.addStretch(1)
        return w

    # ---------------------------------------------------------------- Personality tab

    def _personality_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Agent name:", self.name_edit)
        lay.addLayout(form)

        preset_bar = QHBoxLayout()
        preset_bar.addWidget(QLabel("Presets:"))
        for name in PERSONALITY_PRESETS:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self.persona_edit.setPlainText(PERSONALITY_PRESETS[n]))
            preset_bar.addWidget(b)
        preset_bar.addStretch(1)
        lay.addLayout(preset_bar)

        lay.addWidget(QLabel("How it should talk and behave (saved to personality.txt):"))
        self.persona_edit = QPlainTextEdit()
        self.persona_edit.setPlaceholderText("e.g. You are calm, precise and a little ominous. Keep replies short...")
        lay.addWidget(self.persona_edit, 1)
        note = QLabel("This sets how it talks, not its judgement - it still tests its work, asks before "
                      "anything risky, and refuses unsafe things.")
        note.setStyleSheet("color: #888;")
        note.setWordWrap(True)
        lay.addWidget(note)
        return w

    # ---------------------------------------------------------------- Phone tab

    def _phone_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.serve_check = QCheckBox("Enable phone access (run as a web app)")
        self.serve_check.setFont(QFont("", 11, QFont.Bold))
        lay.addWidget(self.serve_check)
        lay.addWidget(QLabel("Open the agent from your phone's browser. Use Tailscale on both devices to reach "
                             "it from anywhere (Wi-Fi or cellular). The token is the only thing guarding it."))

        form = QFormLayout()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(8765)
        self.token_edit = QLineEdit()
        gen = QPushButton("Generate")
        gen.clicked.connect(lambda: self.token_edit.setText(secrets.token_urlsafe(32)))
        token_row = QHBoxLayout()
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(gen)
        token_wrap = QWidget()
        token_wrap.setLayout(token_row)
        form.addRow("Port:", self.port_spin)
        form.addRow("Login token:", token_wrap)
        lay.addLayout(form)

        warn = QLabel("While serving, the agent auto-approves its tools (nobody's at the keyboard to answer "
                      "prompts), with the overnight guards still on. Add the 'Stay in one folder' habit by "
                      "launching with --overnight if you want it confined.")
        warn.setStyleSheet("color: #c60;")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        lay.addStretch(1)
        return w

    # ---------------------------------------------------------------- Folders tab

    def _folders_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Folders the agent is allowed to change (create, edit, move or delete files in)."))
        self.folders_list = QListWidget()
        lay.addWidget(self.folders_list, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("Add folder...")
        add_btn.clicked.connect(self.add_folder)
        rm_btn = QPushButton("Remove selected")
        rm_btn.clicked.connect(self.remove_folder)
        row.addWidget(add_btn)
        row.addWidget(rm_btn)
        row.addStretch(1)
        lay.addLayout(row)
        note = QLabel("Leave empty to let the agent work anywhere (in normal mode it still asks before each "
                      "change). Add folders to fence it in: it can still read files elsewhere, but it can only "
                      "create, edit, move or delete inside these folders - even over the phone.")
        note.setStyleSheet("color:#888;")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.sticky_check = QCheckBox("Extra-careful mode: sticky re-confirm after the agent reads untrusted content")
        self.sticky_check.setStyleSheet("color:#aab;")
        lay.addWidget(self.sticky_check)

        self.hardened_check = QCheckBox("Hardened Mode: lock the agent down (recommended if you're cautious)")
        self.hardened_check.setStyleSheet("color:#8fd08f; font-weight:bold;")
        lay.addWidget(self.hardened_check)
        hnote = QLabel("One switch that turns on every safety layer at once: the agent can only read AND change "
                       "files inside the allowed folders above, it stays extra-careful for the whole session after "
                       "reading anything from the web or another app, and phone access binds to this PC only (give "
                       "it your Tailscale address if you still want your phone to reach it). Leave it off to run "
                       "wide-open - everything still asks before each change in normal mode.")
        hnote.setStyleSheet("color:#888;")
        hnote.setWordWrap(True)
        lay.addWidget(hnote)
        self.hardened_check.toggled.connect(lambda on: on and self.sticky_check.setChecked(True))

        # --- 0.6.0: the real OS sandbox, a sub-toggle under Hardened. Only enabled if a backend exists. ---
        self.sandbox_check = QCheckBox("Real sandbox: run the agent's shell commands inside an OS box "
                                       "(strongest protection)")
        self.sandbox_check.setStyleSheet("color:#8fd0d0; font-weight:bold;")
        lay.addWidget(self.sandbox_check)

        net_row = QHBoxLayout()
        net_row.addWidget(QLabel("Sandbox network:"))
        self.network_combo = QComboBox()
        for label, val in [("Off (most private)", "off"), ("Loopback only", "loopback"), ("On", "on")]:
            self.network_combo.addItem(label, val)
        net_row.addWidget(self.network_combo)
        net_row.addStretch(1)
        net_wrap = QWidget()
        net_wrap.setLayout(net_row)
        lay.addWidget(net_wrap)

        backend = "none"
        try:
            if _agent_mod:
                backend = _agent_mod.detect_sandbox_backend("auto")
        except Exception:  # noqa: BLE001
            backend = "none"
        # Only Windows Sandbox is a working wall in this build; WSL is detected but its backend isn't
        # wired yet, so don't offer a toggle that would silently fall back to in-process confinement.
        if backend == "windows-sandbox":
            self.sandbox_check.setEnabled(True)
            sbx_msg = ("Available here via Windows Sandbox. When on, run_shell runs inside a box where only "
                       "your allowed folders exist - even if a command breaks out of the agent, it can't "
                       "reach the rest of your disk. Ticking this also turns on Hardened Mode.")
        elif backend == "wsl":
            self.sandbox_check.setEnabled(False)
            sbx_msg = ("WSL is installed, but this build's real sandbox needs the Windows Sandbox feature "
                       "(Windows Pro/Enterprise - search 'Turn Windows features on or off'). A WSL backend "
                       "is coming. Hardened Mode above still gives in-process protection now.")
        else:
            self.sandbox_check.setEnabled(False)
            sbx_msg = ("No OS sandbox backend found on this PC. Turn on the Windows Sandbox feature "
                       "(Windows Pro/Enterprise) to enable this. Until then, Hardened Mode above still "
                       "gives in-process protection.")
        sbx_note = QLabel(sbx_msg)
        sbx_note.setStyleSheet("color:#888;")
        sbx_note.setWordWrap(True)
        lay.addWidget(sbx_note)
        self.sandbox_check.toggled.connect(lambda on: on and self.hardened_check.setChecked(True))
        return w

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder the agent may use")
        if d:
            self.folders_list.addItem(d)

    def remove_folder(self):
        for item in self.folders_list.selectedItems():
            self.folders_list.takeItem(self.folders_list.row(item))

    # ---------------------------------------------------------------- Add-ons tab

    def _addons_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Optional packs. Install only what you want; each is separate from the core agent."))

        gen_box = QGroupBox("Image & sound generation")
        gg = QVBoxLayout(gen_box)
        gg.addWidget(QLabel("Local Stable Diffusion (images), MusicGen (music/SFX) and Piper (speech). "
                            "Needs Python 3.13, and downloads several GB the first time."))
        grow = QHBoxLayout()
        self.gen_btn = QPushButton("Install image & sound pack")
        self.gen_btn.clicked.connect(self.install_gen)
        self.gen_status = QLabel("")
        grow.addWidget(self.gen_btn)
        grow.addWidget(self.gen_status, 1)
        gg.addLayout(grow)
        lay.addWidget(gen_box)

        rea_box = QGroupBox("REA reverse-engineering tools")
        rg = QVBoxLayout(rea_box)
        rg.addWidget(QLabel("Adds the REA toolset (binaries, apps, firmware, web) as an MCP server. "
                            "Runs through npx, so it needs Node.js installed."))
        rrow = QHBoxLayout()
        self.rea_btn = QPushButton("Enable REA tools")
        self.rea_btn.clicked.connect(self.toggle_rea)
        self.rea_status = QLabel("")
        rrow.addWidget(self.rea_btn)
        rrow.addWidget(self.rea_status, 1)
        rg.addLayout(rrow)
        lay.addWidget(rea_box)

        voice_box = QGroupBox("Voice - have Golem speak its replies")
        vg = QVBoxLayout(voice_box)
        vg.addWidget(QLabel("Golem speaks through the orb, which pulses with light as it talks. Uses a "
                            "small self-contained voice engine - no Python, no account, about 80 MB the "
                            "first time. One click below; works on a fresh PC."))
        self.voice_check = QCheckBox("Read Golem's replies out loud")
        vg.addWidget(self.voice_check)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.addItems(PIPER_VOICES)
        vrow.addWidget(self.voice_combo, 1)
        self.voice_install_btn = QPushButton("Install voice")
        self.voice_install_btn.clicked.connect(lambda: self.setup_voice(test=False))
        vrow.addWidget(self.voice_install_btn)
        self.voice_test_btn = QPushButton("Install && test")
        self.voice_test_btn.clicked.connect(lambda: self.setup_voice(test=True))
        vrow.addWidget(self.voice_test_btn)
        vg.addLayout(vrow)
        self.voice_status = QLabel("")
        self.voice_status.setStyleSheet("color:#888;")
        vg.addWidget(self.voice_status)
        self.voice_log = QLabel("")
        self.voice_log.setStyleSheet("color:#888;")
        self.voice_log.setWordWrap(True)
        vg.addWidget(self.voice_log)
        vbrowse = QPushButton("Browse & hear more voices")
        vbrowse.clicked.connect(lambda: webbrowser.open("https://rhasspy.github.io/piper-samples/"))
        vg.addWidget(vbrowse)
        vnote = QLabel("Pick a voice and Install (or Install & test to hear it now). The agent window's "
                       "speaker button toggles speaking too. Only use voices you're allowed to - cloning "
                       "your own voice from a sample is a planned add-on.")
        vnote.setStyleSheet("color:#888;")
        vnote.setWordWrap(True)
        vg.addWidget(vnote)
        lay.addWidget(voice_box)

        stt_box = QGroupBox("Talk to Golem - speak to it with your voice")
        sg = QVBoxLayout(stt_box)
        sg.addWidget(QLabel("Adds a microphone button to the chat window so you can talk instead of type. "
                            "Uses a self-contained local engine (whisper) - no Python, no account, nothing "
                            "sent anywhere. One-time download of about 155 MB."))
        srow = QHBoxLayout()
        self.stt_btn = QPushButton("Install speech-to-text")
        self.stt_btn.clicked.connect(self.setup_stt)
        self.stt_status = QLabel("")
        srow.addWidget(self.stt_btn)
        srow.addWidget(self.stt_status, 1)
        sg.addLayout(srow)
        self.stt_wake_check = QCheckBox("Also listen for the wake word “Hey Golem” (keeps the mic on in the background)")
        sg.addWidget(self.stt_wake_check)
        self.stt_log = QLabel("")
        self.stt_log.setStyleSheet("color:#888;")
        self.stt_log.setWordWrap(True)
        sg.addWidget(self.stt_log)
        swnote = QLabel("With the wake word on, Golem's mic stays on and listens for its name - it only acts "
                        "when you say “Hey Golem”. All on your PC; nothing is sent anywhere. Leave it off to "
                        "talk only when you click the microphone.")
        swnote.setStyleSheet("color:#888;")
        swnote.setWordWrap(True)
        sg.addWidget(swnote)
        lay.addWidget(stt_box)

        orb_box = QGroupBox("Ambient orb")
        og = QVBoxLayout(orb_box)
        og.addWidget(QLabel("A floating ball of data that glows and pulses when Golem is active - "
                            "click-through and always on top. Control it from the tray icon (themes, "
                            "colours, move, quit)."))
        orow = QHBoxLayout()
        self.orb_launch_btn = QPushButton("Launch orb")
        self.orb_launch_btn.clicked.connect(self.launch_orb)
        orow.addWidget(self.orb_launch_btn)
        self.orb_autostart_check = QCheckBox("Start it with the agent (standard)")
        self.orb_autostart_check.setChecked(True)
        orow.addWidget(self.orb_autostart_check)
        orow.addStretch(1)
        og.addLayout(orow)
        lay.addWidget(orb_box)

        routines_box = QGroupBox("Routines - one-click tasks")
        rtg = QVBoxLayout(routines_box)
        rtg.addWidget(QLabel("Save tasks you do often. Each becomes a button in the chat window - click it "
                             "and Golem runs that request. Tick a routine to also run it when Golem starts."))
        self.routines_list = QListWidget()
        self.routines_list.setMaximumHeight(120)
        rtg.addWidget(self.routines_list)
        rtrow = QHBoxLayout()
        add_rt = QPushButton("Add routine")
        add_rt.clicked.connect(self.add_routine)
        del_rt = QPushButton("Remove selected")
        del_rt.clicked.connect(self.remove_routine)
        launch_rt = QPushButton("Toggle 'run at launch'")
        launch_rt.clicked.connect(self.toggle_routine_launch)
        time_rt = QPushButton("Set daily time")
        time_rt.clicked.connect(self.set_routine_time)
        rtrow.addWidget(add_rt)
        rtrow.addWidget(del_rt)
        rtrow.addWidget(launch_rt)
        rtrow.addWidget(time_rt)
        rtrow.addStretch(1)
        rtg.addLayout(rtrow)
        lay.addWidget(routines_box)

        home_box = QGroupBox("Smart home (Home Assistant)")
        hg = QVBoxLayout(home_box)
        hg.addWidget(QLabel("Let Golem control your smart home through Home Assistant (open-source, runs on "
                            "your own network - nothing goes to the cloud). Paste your HA address and a "
                            "long-lived token (in HA: your profile - Long-lived access tokens). Then tick "
                            "“Control my smart home” in this agent's abilities."))
        hrow1 = QHBoxLayout()
        hrow1.addWidget(QLabel("Address:"))
        self.ha_url_edit = QLineEdit()
        self.ha_url_edit.setPlaceholderText("http://homeassistant.local:8123")
        hrow1.addWidget(self.ha_url_edit, 1)
        hg.addLayout(hrow1)
        hrow2 = QHBoxLayout()
        hrow2.addWidget(QLabel("Token:"))
        self.ha_token_edit = QLineEdit()
        self.ha_token_edit.setEchoMode(QLineEdit.Password)
        self.ha_token_edit.setPlaceholderText("long-lived access token")
        hrow2.addWidget(self.ha_token_edit, 1)
        hg.addLayout(hrow2)
        lay.addWidget(home_box)

        presence_box = QGroupBox("Presence")
        pg = QVBoxLayout(presence_box)
        self.tray_presence_check = QCheckBox("Keep Golem running in the tray when I close the chat window")
        pg.addWidget(self.tray_presence_check)
        pnote = QLabel("On: closing the window hides Golem to the tray so it stays ready (the wake word keeps "
                       "listening); quit fully from the tray icon. Off: closing shuts Golem down.")
        pnote.setStyleSheet("color:#888;")
        pnote.setWordWrap(True)
        pg.addWidget(pnote)
        prow = QHBoxLayout()
        self.proactive_check = QCheckBox("Let Golem glance at my screen and offer help (Navi mode)")
        prow.addWidget(self.proactive_check)
        prow.addWidget(QLabel("every"))
        self.proactive_interval = QSpinBox()
        self.proactive_interval.setRange(3, 120)
        self.proactive_interval.setValue(15)
        self.proactive_interval.setSuffix(" min")
        prow.addWidget(self.proactive_interval)
        prow.addStretch(1)
        pg.addLayout(prow)
        pnote2 = QLabel("Golem checks your screen on that interval and pipes up only if something's useful "
                        "(it stays quiet while you're actively using it). All local - the screen never "
                        "leaves your PC. Off by default.")
        pnote2.setStyleSheet("color:#888;")
        pnote2.setWordWrap(True)
        pg.addWidget(pnote2)
        lay.addWidget(presence_box)

        upd_box = QGroupBox("Updates")
        ug = QVBoxLayout(upd_box)
        ug.addWidget(QLabel(f"You're running Golem {VERSION}."))
        self.update_check_box = QCheckBox("Check GitHub for new versions when the Builder opens")
        self.update_check_box.setChecked(True)
        ug.addWidget(self.update_check_box)
        urow = QHBoxLayout()
        chk_btn = QPushButton("Check for updates now")
        chk_btn.clicked.connect(lambda: self.check_for_updates(manual=True))
        urow.addWidget(chk_btn)
        urow.addStretch(1)
        ug.addLayout(urow)
        unote = QLabel("Checking only reads the latest release info. If there's a new version, a bar appears "
                       "at the top - clicking Update now downloads the installer and runs it (upgrades in "
                       "place, no admin, no reinstall). Golem never downloads or runs an update on its own.")
        unote.setStyleSheet("color:#888;")
        unote.setWordWrap(True)
        ug.addWidget(unote)
        lay.addWidget(upd_box)

        self.addon_log = QLabel("")
        self.addon_log.setStyleSheet("color:#888;")
        self.addon_log.setWordWrap(True)
        lay.addWidget(self.addon_log)
        lay.addStretch(1)
        self._refresh_addon_status()
        return w

    def _gen_installed(self) -> bool:
        return (AGENT_DIR / "gen" / ".venv" / "Scripts" / "python.exe").exists()

    def _rea_enabled(self) -> bool:
        try:
            data = json.loads((AGENT_DIR / "mcp_servers.json").read_text(encoding="utf-8"))
            return "rea" in data.get("mcpServers", {})
        except Exception:
            return False

    def _refresh_addon_status(self):
        installed = self._gen_installed()
        self.gen_status.setText("Installed." if installed else "Not installed.")
        self.gen_btn.setText("Reinstall image & sound pack" if installed else "Install image & sound pack")
        on = self._rea_enabled()
        self.rea_status.setText("Enabled." if on else "Not enabled.")
        self.rea_btn.setText("Disable REA tools" if on else "Enable REA tools")
        if hasattr(self, "voice_status"):
            self.voice_status.setText("Voice engine installed." if self._voice_installed()
                                      else "Not installed yet - click Install.")
        if hasattr(self, "stt_status"):
            if self._whisper_installed():
                self.stt_status.setText("Installed - use the microphone button in the chat window.")
                self.stt_btn.setText("Reinstall speech-to-text")
            else:
                self.stt_status.setText("Not installed yet - click Install.")

    def install_gen(self):
        if self.addon_thread:
            return
        genvenv = AGENT_DIR / "gen" / ".venv"
        gpy = str(genvenv / "Scripts" / "python.exe")
        steps = [
            ("Create Python 3.13 environment", ["py", "-3.13", "-m", "venv", str(genvenv)]),
            ("Install PyTorch (CUDA 12.8) - large download",
             [gpy, "-m", "pip", "install", "--index-url", "https://download.pytorch.org/whl/cu128",
              "torch", "torchaudio"]),
            ("Install image/audio libraries",
             [gpy, "-m", "pip", "install", "-r", str(AGENT_DIR / "gen" / "requirements.txt")]),
        ]
        self.gen_btn.setEnabled(False)
        self.addon_log.setText("Installing image & sound pack - this downloads several GB, give it time...")
        self.addon_thread = CmdThread(steps)
        self.addon_thread.line.connect(lambda ln: self.addon_log.setText(ln))
        self.addon_thread.done.connect(self._gen_done)
        self.addon_thread.start()

    def _gen_done(self, code: int):
        self.gen_btn.setEnabled(True)
        self.addon_thread = None
        self.addon_log.setText("Image & sound pack installed." if code == 0
                               else "Install failed - see the line above (is Python 3.13 installed?).")
        self._refresh_addon_status()

    def toggle_rea(self):
        f = AGENT_DIR / "mcp_servers.json"
        try:
            data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except Exception:
            data = {}
        servers = data.setdefault("mcpServers", {})
        if "rea" in servers:
            del servers["rea"]
            msg = "REA tools disabled."
        else:
            servers["rea"] = {"command": "cmd.exe", "args": ["/d", "/c", "npx", "-y", "rea-agents@6.8.0", "mcp"]}
            msg = "REA tools enabled - restart the agent to load them."
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.addon_log.setText(msg)
        self._refresh_addon_status()

    # ---------------------------------------------------------------- load saved values into widgets

    def load_into_widgets(self):
        c = self.cfg
        m = c.get("model", {})
        mode = m.get("mode", "auto")
        self.r_auto.setChecked(mode == "auto")
        self.r_local.setChecked(mode == "local")
        self.r_claude.setChecked(mode == "claude")
        self.text_combo.setCurrentText(m.get("text_model", "hermes3"))
        self.vision_combo.setCurrentText(m.get("vision_model", "gemma4:12b"))
        self.local_combo.setCurrentText(m.get("local_model", "gemma4:12b"))
        self.ctx_spin.setValue(int(m.get("ctx", 65536)))
        for key, cb in self.feature_boxes.items():
            cb.setChecked(bool(c.get("features", {}).get(key, True)))
        pid = c.get("profile", "general")
        pidx = self.profile_combo.findData(pid)
        self.profile_combo.setCurrentIndex(pidx if pidx >= 0 else 0)
        self._profile_desc_update()
        sb = c.get("sandbox", {}) or {}
        if getattr(self, "sandbox_check", None) is not None and self.sandbox_check.isEnabled():
            self.sandbox_check.setChecked(bool(sb.get("enabled", False)))
        if getattr(self, "network_combo", None) is not None:
            nidx = self.network_combo.findData(sb.get("network", "off"))
            self.network_combo.setCurrentIndex(nidx if nidx >= 0 else 0)
        self.name_edit.setText(c.get("name", "Golem"))
        self.persona_edit.setPlainText(c.get("personality", ""))
        self.folders_list.clear()
        self.folders_list.addItems([str(f) for f in c.get("folders", [])])
        self.sticky_check.setChecked(bool(c.get("sticky_taint", False)))
        self.hardened_check.setChecked(bool(c.get("hardened", False)))
        self.update_check_box.setChecked(bool(c.get("check_updates", True)))
        vc = c.get("voice") or {}
        if getattr(self, "voice_check", None) is not None:
            self.voice_check.setChecked(bool(vc.get("enabled", True)))
        if getattr(self, "voice_combo", None) is not None:
            self.voice_combo.setCurrentText(vc.get("voice", "en_US-hfc_female-medium"))
        if getattr(self, "stt_wake_check", None) is not None:
            self.stt_wake_check.setChecked(bool(vc.get("wake", False)))
        self._routines = list(c.get("routines") or [])
        self._refresh_routines_list()
        ha = c.get("home_assistant") or {}
        if getattr(self, "ha_url_edit", None) is not None:
            self.ha_url_edit.setText(ha.get("url", ""))
        if getattr(self, "ha_token_edit", None) is not None:
            self.ha_token_edit.setText(ha.get("token", ""))
        if getattr(self, "tray_presence_check", None) is not None:
            self.tray_presence_check.setChecked(bool(c.get("tray_presence", False)))
        pc = c.get("proactive") or {}
        if getattr(self, "proactive_check", None) is not None:
            self.proactive_check.setChecked(bool(pc.get("enabled", False)))
        if getattr(self, "proactive_interval", None) is not None:
            self.proactive_interval.setValue(int(pc.get("interval_min", 15) or 15))
        if getattr(self, "orb_autostart_check", None) is not None:
            self.orb_autostart_check.setChecked(bool(c.get("orb_autostart", True)))
        if getattr(self, "ollama_install_edit", None) is not None:
            self.ollama_install_edit.setText(c.get("ollama_install_dir", "") or "")
        if getattr(self, "ollama_models_edit", None) is not None:
            self.ollama_models_edit.setText(c.get("ollama_models_dir", "") or "")
        s = c.get("serve", {})
        self.serve_check.setChecked(bool(s.get("enabled", False)))
        self.port_spin.setValue(int(s.get("port", 8765)))
        self.token_edit.setText(s.get("token", ""))
        self._refresh_model_fields()

    # ---------------------------------------------------------------- launching

    def _launch(self, extra_note: str):
        self.save()
        cmd = [agent_python(), str(AGENT_PY), "--config", str(CONFIG_FILE)]
        try:
            subprocess.Popen(cmd, cwd=str(AGENT_DIR),
                             creationflags=(subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0))
            self.flash(extra_note)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not launch", str(e))

    def launch_agent(self):
        # The agent opens in its own desktop chat window (agent_gui.py), never a terminal.
        self.save()
        gui = AGENT_DIR / "agent_gui.py"
        gui_exe = AGENT_DIR / "Golem.exe"  # the installed agent window, if present
        try:
            if gui_exe.exists():
                subprocess.Popen([str(gui_exe)], cwd=str(AGENT_DIR))
            else:
                pyw = agent_python()
                if pyw.endswith("python.exe") and os.path.exists(pyw[:-10] + "pythonw.exe"):
                    pyw = pyw[:-10] + "pythonw.exe"  # no console window for the GUI
                subprocess.Popen([pyw, str(gui)], cwd=str(AGENT_DIR))
            self.flash("Launched the agent in its own window.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not launch", str(e))

    def launch_server(self):
        if not self.serve_check.isChecked():
            self.serve_check.setChecked(True)
        if not self.token_edit.text().strip():
            self.token_edit.setText(secrets.token_urlsafe(32))
        self._launch("Launched the phone server in a new window - open the printed address on your phone.")


def main():
    app = QApplication(sys.argv)
    win = Builder()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
