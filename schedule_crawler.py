"""APScheduler 3.x single-machine service. Explicitly started and stopped by the user."""
import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from filelock import FileLock, Timeout

from crawler_lab.scheduling import ScheduledCrawler, utc_now, validate_schedule, write_state

ROOT = Path(__file__).resolve().parent


def serve(args):
    validate_schedule(args.mode, args.backend, args.interval_seconds, args.book_pages, args.quote_pages)
    (ROOT / "data").mkdir(exist_ok=True)
    with FileLock(str(ROOT / "data/scheduler.lock"), timeout=0):
        worker = ScheduledCrawler(ROOT, args.mode, args.backend, args.book_pages, args.quote_pages)
        state = {"status": "running", "pid": os.getpid(), "mode": args.mode, "backend": args.backend,
                 "interval_seconds": args.interval_seconds, "started_at": utc_now(), "completed_attempts": 0}
        state_lock = threading.Lock()
        stop = threading.Event()

        def collect():
            with state_lock:
                state["active_since"] = utc_now()
                write_state(ROOT, state)
            result = worker.run_once()
            with state_lock:
                state["last_result"] = result
                state["active_since"] = None
                state["completed_attempts"] += 1
                write_state(ROOT, state)
                if args.max_runs and state["completed_attempts"] >= args.max_runs:
                    stop.set()
            print(json.dumps(result, ensure_ascii=False), flush=True)

        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        options = {"seconds": args.interval_seconds, "id": "bounded_crawl", "max_instances": 1,
                   "coalesce": True, "misfire_grace_time": 60}
        if args.run_now:
            options["next_run_time"] = datetime.now(timezone.utc)
        job = scheduler.add_job(collect, "interval", **options)
        scheduler.start()
        print(f"调度已启动：{args.mode}/{args.backend}，间隔{args.interval_seconds}秒；Ctrl+C停止。", flush=True)
        try:
            while not stop.is_set():
                with state_lock:
                    state["heartbeat_at"] = utc_now()
                    state["next_run_at"] = job.next_run_time.isoformat() if job.next_run_time else None
                    write_state(ROOT, state)
                stop.wait(1)
        except KeyboardInterrupt:
            print("正在停止调度，等待当前采集结束……", flush=True)
        finally:
            scheduler.shutdown(wait=True)
            with state_lock:
                state.update(status="stopped", stopped_at=utc_now(), next_run_at=None)
                write_state(ROOT, state)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--backend", choices=["sqlite", "mysql"], default="sqlite")
    parser.add_argument("--interval-seconds", type=int, default=1800)
    parser.add_argument("--book-pages", type=int, default=2)
    parser.add_argument("--quote-pages", type=int, default=2)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0, help="演示验收时完成指定次数后退出；0为持续运行")
    args = parser.parse_args()
    if args.max_runs < 0:
        parser.error("max-runs不能为负数")
    try:
        return serve(args)
    except (ValueError, Timeout) as error:
        print("调度未启动：" + (str(error) if isinstance(error, ValueError) else "已有调度器运行"))
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
