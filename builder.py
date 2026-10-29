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
    QListWidget, QFileDialog,
)

# When frozen into an .exe (PyInstaller), __file__ points into a temp extract folder, so use the
# exe's own location instead. Keep the exe in the Golem folder, next to agent.py and .venv.
if getattr(sys, "frozen", False):
    AGENT_DIR = Path(sys.executable).resolve().parent
else:
    AGENT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = AGENT_DIR / "agent_config.json"
AGENT_PY = AGENT_DIR / "agent.py"

# Keep this list in step with FEATURE_TOOLS in agent.py. (key, label, one-line help)
FEATURES = [
    ("shell",         "Run shell commands",            "PowerShell / bash - the core 'do things on my PC' power."),
    ("files",         "Read & edit files",             "Read, search, create and edit files on disk."),
    ("web",           "Search & read the web",         "Free DuckDuckGo search and fetch whole pages."),
    ("computer",      "Control screen / mouse / keys", "See the screen and click & type. Needs a vision model."),
    ("network_scan",  "Scan my local network",         "List devices on your own Wi-Fi (read-only discovery)."),
    ("godot",         "Godot game testing",            "Import and run Godot 4 projects headless, report errors."),
    ("image_gen",     "Generate images",               "Local Stable Diffusion - sprites and art. Needs gen/ setup."),
    ("audio_gen",     "Generate music / SFX",          "Local MusicGen - music and sound effects. Needs gen/ setup."),
    ("speech",        "Text to speech",                "Local Piper voice. Needs gen/ setup."),
    ("notifications", "Notify my phone",               "Push via ntfy when a job finishes or it gets stuck."),
]

# A few known-good models to offer in the 'pull' box. The user can type any Ollama tag.
SUGGESTED_MODELS = ["gemma4:12b", "hermes3", "qwen2.5:7b", "llama3.2:3b", "llava:13b", "mistral:7b"]

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


def ollama_models() -> list:
    """Names of models already pulled into Ollama, newest first-ish. [] if Ollama isn't found."""
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10,
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
                ["ollama", "pull", self.model],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
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


class Builder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Golem Builder")
        self.resize(720, 760)
        _ico = AGENT_DIR / "icon.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self.pull_thread = None
        self.addon_thread = None
        self._rec = None  # the latest GPU model recommendation
        self.cfg = self.load_config()

        root = QVBoxLayout(self)
        title = QLabel("Golem Builder")
        title.setFont(QFont("", 18, QFont.Bold))
        root.addWidget(title)
        root.addWidget(QLabel("Build a custom AI agent for this PC. Everything below is optional."))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._model_tab(), "Brain")
        self.tabs.addTab(self._features_tab(), "Features")
        self.tabs.addTab(self._folders_tab(), "Folders")
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
            "model": {
                "mode": mode,
                "local_model": self.local_combo.currentText().strip(),
                "text_model": self.text_combo.currentText().strip(),
                "vision_model": self.vision_combo.currentText().strip(),
                "ctx": self.ctx_spin.value(),
            },
            "features": {key: cb.isChecked() for key, cb in self.feature_boxes.items()},
            "serve": {
                "enabled": self.serve_check.isChecked(),
                "host": "0.0.0.0",
                "port": self.port_spin.value(),
                "token": self.token_edit.text().strip(),
            },
            "folders": [self.folders_list.item(i).text() for i in range(self.folders_list.count())],
            "personality": self.persona_edit.toPlainText().strip(),
        }

    def save(self):
        self.cfg = self.collect()
        CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        self.flash(f"Saved to {CONFIG_FILE.name}")

    def flash(self, msg: str):
        self.status.setText(msg)

    # ---------------------------------------------------------------- Brain tab

    def _model_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        orow = QHBoxLayout()
        self.ollama_label = QLabel("Checking Ollama...")
        self.ollama_label.setWordWrap(True)
        get_ollama = QPushButton("Get Ollama")
        get_ollama.clicked.connect(lambda: webbrowser.open("https://ollama.com/download"))
        orow.addWidget(self.ollama_label, 1)
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
        if ollama_running():
            n = len(ollama_models())
            self.ollama_label.setText(f"Ollama: running ✓  ({n} model{'' if n == 1 else 's'} installed)")
            self.ollama_label.setStyleSheet("color:#2a7;")
        else:
            self.ollama_label.setText("Ollama: not found or not running. Click Get Ollama, install and start it, "
                                      "then Refresh list.")
            self.ollama_label.setStyleSheet("color:#c60;")

    # ---------------------------------------------------------------- Features tab

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
        gen.clicked.connect(lambda: self.token_edit.setText(secrets.token_urlsafe(12)))
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
        self.name_edit.setText(c.get("name", "Golem"))
        self.persona_edit.setPlainText(c.get("personality", ""))
        self.folders_list.clear()
        self.folders_list.addItems([str(f) for f in c.get("folders", [])])
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
            self.token_edit.setText(secrets.token_urlsafe(12))
        self._launch("Launched the phone server in a new window - open the printed address on your phone.")


def main():
    app = QApplication(sys.argv)
    win = Builder()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
