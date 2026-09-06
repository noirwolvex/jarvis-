from __future__ import annotations

import queue
import sys
import threading

from dotenv import load_dotenv
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

load_dotenv(override=True)

from core.agent import AgentEvent, JarvisAgent

# Keep the legacy desktop surface compatible while using robust Windows input.
import core.tools as core_tools
from core.desktop_input import paste_text
from core.notepad_automation import open_application_and_type as notepad_aware_open_application_and_type

core_tools._desktop_type = paste_text
core_tools._open_application_and_type = lambda command, text: notepad_aware_open_application_and_type(
    command, text, core_tools._open_application_and_type
)


class ApprovalRequest:
    def __init__(self, tool: str, args: dict) -> None:
        self.tool = tool
        self.args = args
        self.event = threading.Event()
        self.approved = False


class ApprovalBridge:
    def __init__(self) -> None:
        self.requests: queue.Queue[ApprovalRequest] = queue.Queue()

    def request(self, tool: str, args: dict) -> bool:
        request = ApprovalRequest(tool, args)
        self.requests.put(request)
        request.event.wait(timeout=300)
        return request.approved


class Worker(QObject):
    finished = Signal(str)
    event = Signal(object)
    failed = Signal(str)

    def __init__(self, agent: JarvisAgent, prompt: str) -> None:
        super().__init__()
        self.agent = agent
        self.prompt = prompt

    def run(self) -> None:
        try:
            result = self.agent.run(self.prompt, self.event.emit)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ApprovalDialog(QDialog):
    def __init__(self, tool: str, args: dict) -> None:
        super().__init__()
        self.setWindowTitle("JARVIS approval")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"JARVIS wants permission to run:\n\n{tool}"))
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(str(args))
        details.setMaximumHeight(180)
        layout.addWidget(details)
        buttons = QHBoxLayout()
        yes = QPushButton("Approve")
        no = QPushButton("Deny")
        yes.clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        buttons.addWidget(no)
        buttons.addWidget(yes)
        layout.addLayout(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JARVIS — Claude Desktop Agent")
        self.resize(980, 720)
        self.setStyleSheet("""
            QMainWindow { background: #080b12; color: #eaf2ff; }
            QLabel { color: #9fb6d8; }
            QTextEdit, QLineEdit { background: #0e1420; color: #eef6ff; border: 1px solid #243249; border-radius: 12px; padding: 10px; }
            QPushButton { background: #84c8ff; color: #06101c; border: none; border-radius: 10px; padding: 10px 16px; font-weight: 700; }
            QPushButton:hover { background: #a5d7ff; }
        """)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("JARVIS")
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #eaf4ff;")
        subtitle = QLabel("Claude-powered Windows AI agent")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask JARVIS to do something on this PC…")
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("Run")
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        layout.addLayout(row)

        self.approvals = ApprovalBridge()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_approval_requests)
        self.timer.start(100)

        try:
            self.agent = JarvisAgent(approval=self.approvals.request)
            self.write("JARVIS online. Claude tool calling is ready.")
            self.write(f"<span style='color:#7388a6'>{self.agent.provider_info()}</span>")
        except Exception as exc:
            self.agent = None
            self.write(f"Startup error: {exc}")

        self.thread: QThread | None = None
        self.worker: Worker | None = None

    def write(self, text: str) -> None:
        self.log.append(text)

    def process_approval_requests(self) -> None:
        try:
            request = self.approvals.requests.get_nowait()
        except queue.Empty:
            return
        dialog = ApprovalDialog(request.tool, request.args)
        request.approved = dialog.exec() == QDialog.DialogCode.Accepted
        request.event.set()

    def send(self) -> None:
        if not self.agent:
            QMessageBox.critical(self, "JARVIS", "Configure the API key in .env first.")
            return
        prompt = self.input.text().strip()
        if not prompt:
            return
        self.input.clear()
        self.write(f"<b>You:</b> {prompt}")
        self.send_button.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(self.agent, prompt)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(lambda: self.send_button.setEnabled(True))
        self.thread.start()

    def on_event(self, event: AgentEvent) -> None:
        if event.kind == "tool_result":
            self.write(f"<span style='color:#8aa6c9'>Result ({event.tool}):</span><br>{event.message}")
        else:
            self.write(f"<span style='color:#84c8ff'>{event.message}</span>")

    def on_finished(self, result: str) -> None:
        self.write(f"<b>JARVIS:</b> {result}")

    def on_failed(self, error: str) -> None:
        self.write(f"<span style='color:#ff9e9e'><b>Error:</b> {error}</span>")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
