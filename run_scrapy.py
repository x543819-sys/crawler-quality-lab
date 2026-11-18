"""Bounded Scrapy entry; same database, checkpoints and dashboard as the original worker."""
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
from scrapy.crawler import CrawlerProcess

from crawler_lab import storage as db
from crawler_lab.report import export_report
from crawler_lab.scrapy_engine import BooksSpider, ProxyPool, settings

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--backend", choices=["sqlite", "mysql"], default="sqlite")
    parser.add_argument("--book-pages", type=int, default=2)
    parser.add_argument("--demo-scenario", choices=["none", "captcha", "proxy-failure", "rate-limit"], default=None)
    parser.add_argument("--resume")
    parser.add_argument("--proxy-config", type=Path, help="本机HTTP代理配置，不提供公共代理")
    parser.add_argument("--open-report", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.book_pages <= (2 if args.mode == "demo" else 10):
        parser.error("模拟提供1~2页；联网限定1~10页")
    (ROOT / "data").mkdir(exist_ok=True)
    url = "sqlite:///" + (ROOT / "data/crawler.sqlite").as_posix()
    if args.backend == "mysql":
        config = json.loads((ROOT / "local_config.json").read_text(encoding="utf-8")) if (ROOT / "local_config.json").exists() else {}
        url = os.environ.get("CRAWLER_DATABASE_URL") or config.get("database_url", "")
        if not url.startswith("mysql+pymysql://"):
            parser.error("MySQL未配置，不回退到SQLite")
    store = None
    try:
        with FileLock(str(ROOT / "data/worker.lock"), timeout=0):
            store = db.Store(url)
            if args.resume:
                if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", args.resume):
                    raise ValueError("无效批次编号")
                previous = store.rows(db.runs, db.runs.c.id == args.resume)
                if not previous:
                    raise ValueError("批次不存在")
                config = json.loads(previous[0]["config_json"])
                if config.get("engine") != "scrapy":
                    raise ValueError("此入口只续采Scrapy批次")
                mode, run_id = config["mode"], args.resume
                scenario = args.demo_scenario if args.demo_scenario is not None else config["scrapy_scenario"]
            else:
                mode, scenario = args.mode, args.demo_scenario or "none"
                config = {"mode": mode, "book_pages": args.book_pages, "quote_pages": 0,
                          "fault": "none", "engine": "scrapy", "scrapy_scenario": scenario}
                run_id = "r" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:6]
            if mode == "live" and scenario != "none":
                raise ValueError("模拟场景只能用于demo")
            if args.proxy_config and mode != "live":
                raise ValueError("离线演示不读取真实代理配置")
            urls = json.loads(args.proxy_config.read_text(encoding="utf-8")).get("proxies", []) if args.proxy_config else []
            if mode == "demo" and scenario == "proxy-failure":
                urls = ["http://127.0.0.1:19081", "http://127.0.0.1:19082"]
            pool = ProxyPool(urls)
            if not args.resume:
                store.start(run_id, config, [("books", page) for page in range(1, args.book_pages + 1)])
            folder = ROOT / "runs" / run_id
            (folder / "snapshots").mkdir(parents=True, exist_ok=True)
            pending = [row["page"] for row in store.rows(db.tasks, db.tasks.c.run_id == run_id) if row["status"] != "complete"]
            store.log(run_id, "system", 0, "scrapy_started", "scenario=" + scenario)
            process = CrawlerProcess(settings(mode))
            process.crawl(BooksSpider, root=ROOT, folder=folder, store=store, run_id=run_id,
                          mode=mode, scenario=scenario, pages=sorted(pending), pool=pool)
            process.start()
            pages = store.rows(db.tasks, db.tasks.c.run_id == run_id)
            accepted = store.rows(db.observations, db.observations.c.run_id == run_id)
            rejected = store.rows(db.quarantine, db.quarantine.c.run_id == run_id)
            status = "complete" if all(p["status"] == "complete" for p in pages) and not rejected else ("partial" if accepted else "failed")
            store.finish(run_id, status)
            stats = export_report(store, run_id, folder)
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            print("报告：", folder / "report.html")
            if args.open_report:
                webbrowser.open((folder / "report.html").as_uri())
            return 0 if status == "complete" else 1
    except Timeout:
        print("已有采集任务运行，请等待完成。")
        return 2
    except Exception as error:
        print("Scrapy启动未完成：", type(error).__name__)
        return 2
    finally:
        if store is not None:
            store.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
