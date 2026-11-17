"""Standalone single-process scheduling, intentionally separate from Streamlit reruns."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_schedule(mode, backend, interval_seconds, book_pages, quote_pages):
    if mode not in ("demo", "live") or backend not in ("sqlite", "mysql"):
        raise ValueError("模式或数据库无效")
    minimum = 5 if mode == "demo" else 900
    if interval_seconds < minimum:
        raise ValueError(f"{mode}模式的调度间隔至少{minimum}秒")
    if not 1 <= book_pages <= (2 if mode == "demo" else 50):
        raise ValueError("图书页数超出允许范围")
    if not 1 <= quote_pages <= (2 if mode == "demo" else 10):
        raise ValueError("语录页数超出允许范围")


class ScheduledCrawler:
    def __init__(self, root, mode, backend, book_pages=2, quote_pages=2):
        self.root = Path(root)
        self.mode, self.backend = mode, backend
        self.book_pages, self.quote_pages = book_pages, quote_pages
        self.folder = self.root / "data/scheduler"
        self.folder.mkdir(parents=True, exist_ok=True)

    def run_once(self):
        """Reuse the exact CLI, locking, validation and checkpoints of manual collection."""
        started = utc_now()
        attempt_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:6]
        record = {"attempt_id": attempt_id, "mode": self.mode, "backend": self.backend,
                  "started_at": started, "run_id": None}
        probe = FileLock(str(self.root / "data/worker.lock"), timeout=0)
        try:
            probe.acquire()
        except Timeout:
            record.update(status="skipped_busy", finished_at=utc_now(), exit_code=None)
            return self.save_result(record)
        else:
            probe.release()
        log_path = self.folder / (attempt_id + ".log")
        command = [sys.executable, "-u", str(self.root / "launcher.py"), "--mode", self.mode,
                   "--backend", self.backend, "--book-pages", str(self.book_pages),
                   "--quote-pages", str(self.quote_pages)]
        try:
            with log_path.open("w", encoding="utf-8") as output:
                process = subprocess.run(command, cwd=self.root, stdout=output, stderr=subprocess.STDOUT,
                                         check=False)
            content = log_path.read_text(encoding="utf-8", errors="replace")
            import re
            match = re.search(r"新批次：\s*(r[A-Za-z0-9_-]+)", content)
            record["run_id"] = match.group(1) if match else None
            status = {0: "complete", 1: "partial_or_failed", 2: "not_started", 130: "interrupted"}.get(process.returncode, "failed")
            if "已有采集任务运行" in content:
                status = "skipped_busy"
            record.update(status=status, exit_code=process.returncode)
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__, exit_code=None)
        record.update(finished_at=utc_now(), log_file=log_path.name)
        return self.save_result(record)

    def save_result(self, record):
        (self.folder / (record["attempt_id"] + ".json")).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record


def write_state(root, state):
    folder = Path(root) / "data/scheduler"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ("state_" + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "state.json")
