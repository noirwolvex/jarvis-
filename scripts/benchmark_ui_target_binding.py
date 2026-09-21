"""Count UIA provider reads using detached fixtures; never queries or controls Windows."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import semantic_ui_tools as ui


class Info:
    def __init__(self, counter, runtime_id):
        self.counter = counter
        self.values = dict(name="Fixture", control_type="Edit", automation_id="editor",
                           runtime_id=runtime_id, process_id=42)

    def __getattr__(self, name):
        self.counter[0] += 1
        return self.values[name]


class Control:
    def __init__(self, counter, runtime_id):
        self.counter = counter
        self.element_info = Info(counter, runtime_id)
        self.handle = 7

    def rectangle(self):
        self.counter[0] += 1
        return SimpleNamespace(left=0, top=0, right=100, bottom=100)

    def flag(self):
        self.counter[0] += 1
        return True

    is_enabled = is_visible = is_selected = has_keyboard_focus = flag


def measure():
    counter = [0]
    window, control = Control(counter, [1]), Control(counter, [2])
    # Exact pre-optimization binding implementation, retained only as a fixture baseline.
    before = {"window_hwnd": int(window.handle), "window_identity": ui._node_identity(window),
              "window_process_id": ui._meta(window).get("process_id"), "identity": ui._node_identity(control),
              "control": ui._meta(control), "generation": ui._SNAPSHOTS.generation}
    old_reads = counter[0]
    counter[0] = 0
    after = ui._target_binding(window, control)
    assert before == after, "Optimization must preserve binding evidence"
    return {"fixture_provider_reads_before": old_reads, "fixture_provider_reads_after": counter[0],
            "identical_binding": True, "live_uia_latency_measured": False}


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
