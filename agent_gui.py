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
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame,
)

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
        self.setWindowTitle(f"{self.name} - Golem")
        self.resize(820, 720)
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.setStyleSheet(f"""
            QWidget {{ background:{BG}; color:{TEXT}; font-family:'Segoe UI','Inter',sans-serif; font-size:14px; }}
            QScrollArea {{ border:none; }}
            QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:#2c3342; border-radius:5px; min-height:30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
        """)

        self.proc = None
        self.reader = None
        self.cur_agent = None     # the QLabel of the agent's in-progress message bubble
        self.agent_text = ""      # text committed to that bubble
        self.line_buf = ""        # output not yet split into a full line
        self._user_stopped = False  # true when the user pressed Stop (so "stopped" reads intentionally)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        holder = QWidget()
        self.col = QVBoxLayout(holder)
        self.col.setAlignment(Qt.AlignTop)
        self.col.setContentsMargins(16, 12, 16, 12)
        self.col.setSpacing(8)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _mn, mx: self.scroll.verticalScrollBar().setValue(mx))

        root.addWidget(self._permission_bar())
        root.addWidget(self._composer())

        self.start_agent()

    # ---------------------------------------------------------------- building blocks

    def _header(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(f"background:{PANEL};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 16, 10)
        avatar = QLabel()
        if ICON.exists():
            avatar.setPixmap(QPixmap(str(ICON)).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(avatar)
        box = QVBoxLayout()
        box.setSpacing(0)
        nm = QLabel(self.name)
        nm.setStyleSheet(f"color:{TEXT}; font-size:17px; font-weight:600;")
        self.sub = QLabel("starting up...")
        self.sub.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        box.addWidget(nm)
        box.addWidget(self.sub)
        h.addLayout(box)
        h.addStretch(1)
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
        bar = QFrame()
        bar.setStyleSheet(f"background:{PANEL}; border-top:1px solid #20242e;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 12, 16, 14)
        h.setSpacing(10)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(f"Message {self.name}...")
        self.entry.setStyleSheet(
            "background:#1b1f27; color:#eef0f5; padding:11px 14px; border:1px solid #2a2f3a; "
            "border-radius:12px; font-size:14px;")
        self.entry.returnPressed.connect(self.send)
        h.addWidget(self.entry, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"background:{ACCENT}; color:white; padding:11px 20px; border:none; border-radius:12px; font-weight:600;")
        self.send_btn.clicked.connect(self.send)
        h.addWidget(self.send_btn)
        return bar

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
        self.agent_text += (("\n" if self.agent_text else "") + line)
        self.cur_agent.setText(self.agent_text)

    def _preview_agent(self, tail: str):
        if self.cur_agent is None:
            self.cur_agent = self._bubble("", "agent")
            self.agent_text = ""
        self.cur_agent.setText(self.agent_text + (("\n" if self.agent_text else "") + tail))

    # ---------------------------------------------------------------- input

    def send(self):
        text = self.entry.text().strip()
        if not text:
            return
        if not self._running():
            self._bubble("The agent isn't running - click Restart.", "system")
            return
        self.entry.clear()
        self.perm_bar.setVisible(False)
        self.cur_agent, self.agent_text = None, ""   # next agent output starts a fresh bubble
        self._bubble(text, "user")
        self._write(text)

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
        self._terminate()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = AgentWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
