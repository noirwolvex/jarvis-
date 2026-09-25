from __future__ import annotations

import json
import queue
import sys
import threading

from dotenv import load_dotenv
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
from core.full_access_bridge import build_full_access_agent, run_agent_mission

# Keep the legacy desktop surface compatible while using robust Windows input.
import core.tools as core_tools
from core.desktop_input import paste_text
from core.notepad_automation import open_application_and_type as notepad_aware_open_application_and_type

core_tools._desktop_type = paste_text
_original_open_application_and_type = core_tools._open_application_and_type
core_tools._open_application_and_type = lambda command, text: notepad_aware_open_application_and_type(
    command, text, _original_open_application_and_type
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

    def __init__(self, agent: JarvisAgent, prompt: str, full_access: bool = False) -> None:
        super().__init__()
        self.agent = agent
        self.prompt = prompt
        self.full_access = full_access

    def run(self) -> None:
        try:
            if self.full_access:
                payload = run_agent_mission(
                    self.agent,
                    self.prompt,
                    emit=self.event.emit,
                    allow_shell=False,
                )
                if not payload.get("ok"):
                    raise RuntimeError(
                        str(payload.get("result") or payload.get("error") or "Full Access mission did not complete")
                    )
                result = str(payload.get("result") or "")
            else:
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
            QTextEdit, QLineEdit, QComboBox { background: #0e1420; color: #eef6ff; border: 1px solid #243249; border-radius: 12px; padding: 10px; }
            QComboBox::drop-down { border: none; width: 28px; }
            QPushButton { background: #84c8ff; color: #06101c; border: none; border-radius: 10px; padding: 10px 16px; font-weight: 700; }
            QPushButton:hover { background: #a5d7ff; }
            QPushButton:disabled, QComboBox:disabled { color: #66768d; background: #0b1019; }
        """)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("JARVIS")
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #eaf4ff;")
        subtitle = QLabel("Claude-powered Windows AI agent")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        access_row = QHBoxLayout()
        access_label = QLabel("Access mode")
        access_label.setStyleSheet("font-weight: 700; color: #c8dcf7;")
        self.access_mode = QComboBox()
        self.access_mode.addItem("Restricted", "restricted")
        self.access_mode.addItem("Standard", "standard")
        self.access_mode.addItem("Full Access", "full")
        self.access_mode.setMinimumWidth(190)
        self.access_mode.setEnabled(False)
        self.access_mode.currentIndexChanged.connect(self.change_access_mode)
        self.access_detail = QLabel("Permission engine not initialized")
        access_row.addWidget(access_label)
        access_row.addWidget(self.access_mode)
        access_row.addWidget(self.access_detail, 1)
        layout.addLayout(access_row)

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

        self.full_access_session = False
        try:
            self.agent = JarvisAgent(approval=self.approvals.request)
            self.sync_access_mode()
            self.access_mode.setEnabled(True)
            self.write("JARVIS online. Claude tool calling is ready.")
            self.write(f"<span style='color:#7388a6'>{self.agent.provider_info()}</span>")
            self.write_access_summary()
        except Exception as exc:
            self.agent = None
            self.write(f"Startup error: {exc}")

        self.thread: QThread | None = None
        self.worker: Worker | None = None

    def write(self, text: str) -> None:
        self.log.append(text)

    def sync_access_mode(self) -> None:
        if not self.agent:
            return
        mode = self.agent.tools.permissions.access_mode
        index = self.access_mode.findData(mode)
        self.access_mode.blockSignals(True)
        if index >= 0:
            self.access_mode.setCurrentIndex(index)
        self.access_mode.blockSignals(False)
        self.update_access_detail()

    def update_access_detail(self) -> None:
        if not self.agent:
            self.access_detail.setText("Permission engine not initialized")
            return
        policy = self.agent.tools.permissions.summary()
        if policy["mode"] == "restricted":
            detail = "Observe/read oriented · writes and network disabled"
        elif policy["mode"] == "full":
            detail = "Modern Rust Full Access pipeline · terminal disabled in this legacy window"
        else:
            detail = "Configured policy · scoped write permissions"
        self.access_detail.setText(detail)

    def write_access_summary(self) -> None:
        if not self.agent:
            return
        policy = self.agent.tools.permissions.summary()
        enabled = [
            name for name, key in [
                ("network", "allow_network"),
                ("filesystem-write", "allow_filesystem_write"),
                ("git-write", "allow_git_write"),
                ("browser-write", "allow_browser_write"),
                ("shell", "allow_shell"),
                ("destructive", "allow_destructive"),
            ] if policy[key] and not (self.full_access_session and name == "shell")
        ]
        terminal = " · terminal=disabled here" if self.full_access_session else ""
        self.write(
            f"<span style='color:#84c8ff'><b>Access:</b> {policy['mode']} · "
            f"enabled={', '.join(enabled) if enabled else 'read/observe only'} · "
            f"approval={'on' if policy['require_approval'] else 'off'}{terminal}</span>"
        )

    def change_access_mode(self, index: int) -> None:
        if not self.agent or index < 0:
            return
        requested = self.access_mode.itemData(index)
        permissions = self.agent.tools.permissions
        if requested == permissions.access_mode:
            self.update_access_detail()
            return

        if requested == "full":
            response = QMessageBox.question(
                self,
                "Enable Full Access?",
                "Full Access will switch this window to the same guarded Full Access agent used by "
                "the modern Control Center: semantic UI/CDP routing, verification, recovery, and "
                "strict Rust native input.\n\n"
                "It will not fall back to the legacy PyAutoGUI execution path. The managed Rust "
                "runtime must already be ready. Terminal commands remain disabled in this legacy "
                "window; use the Control Center for separately authorized terminal access.\n\n"
                "Enable Full Access?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                self.sync_access_mode()
                return
            try:
                candidate = build_full_access_agent()
                from core.rust_engine import native_engine_status
                status = json.loads(native_engine_status())
                if status.get("backend") != "rust" or status.get("rust_input_ready") is not True:
                    raise RuntimeError(
                        "Full Access requires the managed Rust runtime. Start JARVIS with "
                        "'npm run dev' or 'npm run start' and use the Control Center, or launch "
                        "this UI from an environment provisioned by that runtime."
                    )
            except Exception as exc:
                QMessageBox.critical(self, "JARVIS Full Access", str(exc))
                self.sync_access_mode()
                return
            self.agent = candidate
            self.full_access_session = True
        else:
            try:
                if self.full_access_session:
                    from core.process_control import set_cancellation
                    candidate = JarvisAgent(approval=self.approvals.request)
                    candidate.tools.permissions.set_access_mode(requested)
                    set_cancellation(lambda: False)
                    self.agent = candidate
                    self.full_access_session = False
                else:
                    permissions.set_access_mode(requested)
            except (RuntimeError, ValueError) as exc:
                QMessageBox.critical(self, "JARVIS", str(exc))
                self.sync_access_mode()
                return

        self.sync_access_mode()
        self.write_access_summary()

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
        self.access_mode.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(self.agent, prompt, full_access=self.full_access_session)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.on_thread_finished)
        self.thread.start()

    def on_thread_finished(self) -> None:
        self.send_button.setEnabled(True)
        self.access_mode.setEnabled(self.agent is not None)

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
