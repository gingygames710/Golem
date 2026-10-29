"""pc-agent desktop - your agent in its own window.

A plain chat window for the agent, so you never touch a terminal. It runs your configured
agent (from agent_config.json) and shows the conversation; type in the box to talk to it,
and when it asks to do something on your PC, answer with the Allow / Deny / Always buttons.

Runs either way with no change:
  - development: launches  python agent.py --config agent_config.json --chat
  - installed:  launches  pc-agent-core.exe --config agent_config.json --chat   (no Python needed)
"""

import codecs
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QPlainTextEdit,
)

# When frozen into an .exe, __file__ is a temp extract dir; use the exe's own folder instead.
if getattr(sys, "frozen", False):
    AGENT_DIR = Path(sys.executable).resolve().parent
else:
    AGENT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = AGENT_DIR / "agent_config.json"
AGENT_PY = AGENT_DIR / "agent.py"
CORE_EXE = AGENT_DIR / "pc-agent-core.exe"  # the frozen agent, present in an installed copy

ANSI = re.compile(r"\x1b\[[0-9;]*m")            # colour codes to strip from the console output
PERMISSION_HINT = re.compile(r"\[y\]es", re.I)   # the agent's permission prompt marker
CREATE_NO_WINDOW = 0x08000000                    # keep the child process from flashing a console


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
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("name") or "pc-agent"
    except Exception:
        return "pc-agent"


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


class AgentWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.name = agent_name()
        self.setWindowTitle(f"{self.name} - pc-agent")
        self.resize(780, 660)
        _ico = AGENT_DIR / "icon.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self.setStyleSheet("background:#14161a; color:#d6d9df;")
        self.proc = None
        self.reader = None

        lay = QVBoxLayout(self)
        header = QLabel(self.name)
        header.setFont(QFont("", 15, QFont.Bold))
        header.setStyleSheet("color:#e8eaf0;")
        lay.addWidget(header)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 10))
        self.view.setStyleSheet("background:#0e1013; color:#cfd3da; border:1px solid #2a2e36; padding:6px;")
        lay.addWidget(self.view, 1)

        # Permission bar - hidden until the agent asks to do something.
        self.perm_bar = QWidget()
        pb = QHBoxLayout(self.perm_bar)
        pb.setContentsMargins(0, 0, 0, 0)
        self.perm_label = QLabel("The agent wants to do something on your PC. Allow it?")
        self.perm_label.setStyleSheet("color:#f0c674;")
        pb.addWidget(self.perm_label, 1)
        for text, letter, color in [("Allow", "y", "#2e7d32"), ("Always", "a", "#1565c0"),
                                    ("Deny", "n", "#7a2e2e")]:
            b = QPushButton(text)
            b.setStyleSheet(f"background:{color}; color:white; padding:6px 14px; border:none; border-radius:4px;")
            b.clicked.connect(lambda _=False, l=letter: self.answer(l))
            pb.addWidget(b)
        self.perm_bar.setVisible(False)
        lay.addWidget(self.perm_bar)

        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Type a message and press Enter...")
        self.entry.setStyleSheet("background:#1b1e24; color:#eceef2; padding:8px; border:1px solid #2a2e36; border-radius:4px;")
        self.entry.returnPressed.connect(self.send)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.restart_btn = QPushButton("Restart")
        self.restart_btn.clicked.connect(self.restart)
        row.addWidget(self.entry, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.restart_btn)
        lay.addLayout(row)

        self.start_agent()

    # ---------------------------------------------------------------- process

    def _running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start_agent(self):
        if not CONFIG_FILE.exists():
            self.append_line(f"[No {CONFIG_FILE.name} yet - open the Builder and click Save first.]")
            return
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # stream output promptly into the window
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                agent_command(), cwd=str(AGENT_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, env=env, creationflags=flags,
            )
        except Exception as e:  # noqa: BLE001
            self.append_line(f"[Could not start the agent: {e}]")
            return
        self.reader = Reader(self.proc)
        self.reader.chunk.connect(self.on_chunk)
        self.reader.ended.connect(
            lambda: self.append_line("\n[the agent has stopped - click Restart to run it again]"))
        self.reader.start()
        self.append_line(f"[starting {self.name}...]")

    def restart(self):
        self._terminate()
        self.view.clear()
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

    def on_chunk(self, text: str):
        self.append_text(ANSI.sub("", text))
        if PERMISSION_HINT.search(text):   # the agent is asking to run a tool
            self.perm_bar.setVisible(True)

    def send(self):
        text = self.entry.text().strip()
        if not text:
            return
        if not self._running():
            self.append_line("[The agent isn't running - click Restart.]")
            return
        self.entry.clear()
        self.perm_bar.setVisible(False)
        self.append_text(f"\n> {text}\n")
        self._write(text)

    def answer(self, letter: str):
        """Write a permission answer (y / n / a) to the running agent."""
        self.perm_bar.setVisible(False)
        if self._running():
            self._write(letter)

    def _write(self, line: str):
        try:
            self.proc.stdin.write((line + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            self.append_line("[Could not reach the agent - click Restart.]")

    # ---------------------------------------------------------------- view helpers

    def append_text(self, text: str):
        self.view.moveCursor(QTextCursor.End)
        self.view.insertPlainText(text)
        self.view.moveCursor(QTextCursor.End)

    def append_line(self, line: str):
        self.append_text(line + "\n")

    def closeEvent(self, event):
        self._terminate()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = AgentWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
