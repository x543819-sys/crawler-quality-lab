"""PyCharm entry point. Default: bounded live crawl, not simulated success."""
import argparse
import json
import os
import re
import sys
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from crawler_lab import storage as db
from crawler_lab.fetchers import DemoFetcher, LiveFetcher
from crawler_lab.parsers import PARSERS, validate
from crawler_lab.report import export_report

ROOT = Path(__file__).resolve().parent


def execute(args, store, fetcher_factory=None):
    if args.resume:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", args.resume):
            raise ValueError("批次编号格式无效")
        found = store.rows(db.runs, db.runs.c.id == args.resume)
        if not found:
            raise ValueError("当前数据库中不存在该批次")
        run_id, config = args.resume, json.loads(found[0]["config_json"])
        print("继续批次：", run_id, "沿用原配置；已提交页面不重复入库", flush=True)
    else:
        config = {"mode": args.mode, "book_pages": args.book_pages,
                  "quote_pages": args.quote_pages, "fault": args.fault}
        run_id = "r" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:6]
        plan = [(source, p) for source, count in [("books", args.book_pages), ("quotes", args.quote_pages)] for p in range(1, count + 1)]
        store.start(run_id, config, plan)
        print("新批次：", run_id, flush=True)
    folder = ROOT / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "snapshots").mkdir(exist_ok=True)

    def log(source, page, kind, detail):
        store.log(run_id, source, page, kind, detail)

    fetcher = fetcher_factory(log) if fetcher_factory else (
        DemoFetcher(ROOT, log, config["fault"]) if config["mode"] == "demo" else LiveFetcher(ROOT, log))
    processed, interrupted = 0, False
    try:
        pending = sorted(store.rows(db.tasks, db.tasks.c.run_id == run_id), key=lambda t: (t["source"], t["page"]))
        for task in pending:
            if task["status"] == "complete":
                continue
            source, page = task["source"], task["page"]
            url = f"https://books.toscrape.com/catalogue/page-{page}.html" if source == "books" else f"https://quotes.toscrape.com/js/page/{page}/"
            snapshot = folder / "snapshots" / f"{source}_{page}.html"
            try:
                print(f"采集 {source} 第{page}页", flush=True)
                document = fetcher.get(source, page, url)
                snapshot.write_text(document, encoding="utf-8")
                rows = PARSERS[source](document, url)
                counts = store.save_page(run_id, config["mode"], source, page, str(snapshot.relative_to(ROOT)),
                                         [(row, validate(source, row)) for row in rows])
                log(source, page, "page_committed", json.dumps(counts))
                print("  完成：", counts, flush=True)
            except Exception as error:
                document = getattr(error, "document", "")
                if document:
                    snapshot.write_text(document, encoding="utf-8")
                store.failed(run_id, source, page, f"{type(error).__name__}: {error}",
                             str(snapshot.relative_to(ROOT)) if snapshot.exists() else "")
                print(f"  已记录失败：{type(error).__name__}: {error}", flush=True)
            processed += 1
            if args.stop_after_pages and processed >= args.stop_after_pages:
                interrupted = True
                log("system", 0, "controlled_stop", "按参数主动暂停，用于验证续采")
                break
    except KeyboardInterrupt:
        interrupted = True
        log("system", 0, "interrupted", "用户中断，可使用批次编号继续")
    finally:
        try:
            fetcher.close()
        except Exception as error:
            log("system", 0, "browser_close_error", type(error).__name__)
    all_tasks = store.rows(db.tasks, db.tasks.c.run_id == run_id)
    observations = store.rows(db.observations, db.observations.c.run_id == run_id)
    rejected = store.rows(db.quarantine, db.quarantine.c.run_id == run_id)
    status = "interrupted" if interrupted else (
        "complete" if all(t["status"] == "complete" for t in all_tasks) and not rejected
        else ("partial" if observations else "failed"))
    store.finish(run_id, status)
    stats = export_report(store, run_id, folder)
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    print("报告：", folder / "report.html", flush=True)
    if args.open_report:
        webbrowser.open((folder / "report.html").as_uri())
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "demo"], default="live")
    parser.add_argument("--backend", choices=["sqlite", "mysql"], default="sqlite")
    parser.add_argument("--book-pages", type=int, default=2)
    parser.add_argument("--quote-pages", type=int, default=2)
    parser.add_argument("--fault", choices=["none", "dirty", "selector", "timeout"], default="none")
    parser.add_argument("--resume", help="继续指定批次，跳过已完成页面")
    parser.add_argument("--stop-after-pages", type=int, default=0, help="主动暂停，验证断点续采")
    parser.add_argument("--open-report", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.book_pages <= 50 or not 1 <= args.quote_pages <= 10:
        parser.error("图书页数须为1~50；语录页数须为1~10")
    if args.mode == "demo" and (args.book_pages > 2 or args.quote_pages > 2):
        parser.error("离线演示只提供每个来源2页模拟数据")
    if args.mode == "live" and args.fault != "none":
        parser.error("模拟故障只能用于 --mode demo")
    if args.stop_after_pages < 0:
        parser.error("stop-after-pages不能为负数")
    (ROOT / "data").mkdir(exist_ok=True)
    if args.backend == "sqlite":
        url = "sqlite:///" + (ROOT / "data/crawler.sqlite").as_posix()
    else:
        config_file = ROOT / "local_config.json"
        local = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
        url = os.environ.get("CRAWLER_DATABASE_URL") or local.get("database_url")
        if not url or not url.startswith("mysql+pymysql://"):
            parser.error("MySQL需要本机连接配置；见README，不会静默改用SQLite")
    try:
        with FileLock(str(ROOT / "data/worker.lock"), timeout=0):
            store = db.Store(url)
            try:
                stats = execute(args, store)
            finally:
                store.dispose()
        return 0 if stats["status"] == "complete" else (130 if stats["status"] == "interrupted" else 1)
    except Timeout:
        print("已有采集任务运行，请等待完成。", file=sys.stderr)
        return 2
    except Exception as error:
        # Never print a connection URL/password in a user-facing report.
        print(f"启动失败（{type(error).__name__}）。请确认依赖、数据库连接和文件权限。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
