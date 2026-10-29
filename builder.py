"""pc-agent Builder - a desktop GUI that builds a custom agent for you.

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
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton, QButtonGroup, QComboBox,
    QPlainTextEdit, QSpinBox, QGroupBox, QTabWidget, QMessageBox, QFrame,
)

# When frozen into an .exe (PyInstaller), __file__ points into a temp extract folder, so use the
# exe's own location instead. Keep the exe in the pc-agent folder, next to agent.py and .venv.
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


class Builder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pc-agent Builder")
        self.resize(720, 760)
        _ico = AGENT_DIR / "icon.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self.pull_thread = None
        self.cfg = self.load_config()

        root = QVBoxLayout(self)
        title = QLabel("pc-agent Builder")
        title.setFont(QFont("", 18, QFont.Bold))
        root.addWidget(title)
        root.addWidget(QLabel("Build a custom AI agent for this PC. Everything below is optional."))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._model_tab(), "Brain")
        self.tabs.addTab(self._features_tab(), "Features")
        self.tabs.addTab(self._personality_tab(), "Personality")
        self.tabs.addTab(self._phone_tab(), "Phone")
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

    # ---------------------------------------------------------------- config I/O

    def load_config(self) -> dict:
        try:
            sys.path.insert(0, str(AGENT_DIR))
            import agent  # reuse the single source of truth for the default shape
            base = agent.default_config()
        except Exception:
            base = {"name": "pc-agent",
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
            "name": self.name_edit.text().strip() or "pc-agent",
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
        self.name_edit.setText(c.get("name", "pc-agent"))
        self.persona_edit.setPlainText(c.get("personality", ""))
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
        gui_exe = AGENT_DIR / "pc-agent.exe"  # the installed agent window, if present
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
