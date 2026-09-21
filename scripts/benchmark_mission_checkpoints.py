"""Offline mission checkpoint benchmark; never opens applications or sends input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.autonomous_orchestrator import AutonomousTaskOrchestrator
from core.execution_telemetry import ToolResult


def measure(steps: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="jarvis-checkpoint-benchmark-") as directory:
        orchestrator = AutonomousTaskOrchestrator(directory)
        orchestrator.strict_order = True
        task = orchestrator.begin("Synthetic ordered mission; no device execution")
        orchestrator.set_plan([{"id": f"step-{i}", "description": f"Fixture {i}"} for i in range(steps)])
        publications = []
        orchestrator.on_task_graph = lambda graph: publications.append(len(graph))
        latencies = []
        for i in range(steps):
            started = time.perf_counter()
            step_id = f"step-{i}"
            orchestrator.update_step(step_id, "running")
            orchestrator.start_action("fixture_write", {"index": i})
            orchestrator.record_tool("fixture_write", {"index": i}, ToolResult(
                "VERIFIED: " + "synthetic evidence " * 80,
                {"backend": "fixture", "operations": []}), 0, 1, mutation=True)
            orchestrator.verify(f"{step_id}: fixture outcome", True, "Fixture readback matched")
            orchestrator.update_step(step_id, "completed")
            latencies.append((time.perf_counter() - started) * 1000)
        restored = AutonomousTaskOrchestrator(directory).restore(task.task_id)
        assert all(step.status == "completed" for step in restored.plan)
        return {"total_ms": round(sum(latencies), 3), "median_step_ms": round(statistics.median(latencies), 3),
                "p95_step_ms": round(sorted(latencies)[int((len(latencies) - 1) * .95)], 3),
                "durable_publications": len(publications),
                "checkpoint_bytes": (Path(directory) / f"{task.task_id}.json").stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=50, choices=range(10, 101), metavar="10..100")
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11), metavar="1..10")
    args = parser.parse_args()
    results = [measure(args.steps) for _ in range(args.repeats)]
    print(json.dumps({"steps": args.steps, "runs": results,
                      "median_total_ms": round(statistics.median(row["total_ms"] for row in results), 3)}, indent=2))
