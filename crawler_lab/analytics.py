"""Read-only dashboard queries. Opening the dashboard never creates schema or runs a crawl."""
import json
import os
from collections import Counter
from pathlib import Path

import pandas as pd
from sqlalchemy import URL, create_engine, func, select

from . import storage as db


def reader_engine(root, backend):
    root = Path(root)
    if backend == "sqlite":
        path = root / "data/crawler.sqlite"
        if not path.is_file():
            raise FileNotFoundError("尚无SQLite数据库，请先运行01离线演示。")
        url = URL.create("sqlite", database="file:" + path.as_posix(),
                         query={"mode": "ro", "uri": "true"})
        return create_engine(url)
    if backend != "mysql":
        raise ValueError("不支持的数据库")
    config_path = root / "local_config.json"
    local = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    url = os.environ.get("CRAWLER_DATABASE_URL") or local.get("database_url", "")
    if not url.startswith("mysql+pymysql://"):
        raise ValueError("MySQL连接尚未配置，请查看README的MySQL章节。")
    return create_engine(url, connect_args={"connect_timeout": 3, "read_timeout": 10})


def _begin_read(connection):
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN")
    else:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def history(engine, mode, limit=200):
    """Consistent snapshot, bounded recent batches; no multiplication across joins."""
    with engine.connect() as connection:
        _begin_read(connection)
        runs = [dict(row) for row in connection.execute(select(db.runs).where(
            db.runs.c.mode == mode).order_by(db.runs.c.started_at.desc(), db.runs.c.id.desc()).limit(limit)).mappings()]
        if not runs:
            return pd.DataFrame()
        ids = [row["id"] for row in runs]
        pages = list(connection.execute(select(db.tasks).where(db.tasks.c.run_id.in_(ids))).mappings())
        observed = connection.execute(select(db.observations.c.run_id, db.observations.c.outcome,
            func.count()).where(db.observations.c.run_id.in_(ids)).group_by(
                db.observations.c.run_id, db.observations.c.outcome)).all()
        rejected = dict(connection.execute(select(db.quarantine.c.run_id, func.count()).where(
            db.quarantine.c.run_id.in_(ids)).group_by(db.quarantine.c.run_id)).all())
    outcome_counts = {(run_id, outcome): count for run_id, outcome, count in observed}
    result = []
    for run in runs:
        tasks = [page for page in pages if page["run_id"] == run["id"]]
        parsed = sum(page["row_count"] or 0 for page in tasks)
        counts = {name: outcome_counts.get((run["id"], name), 0) for name in ("new", "updated", "unchanged")}
        accepted = sum(counts.values())
        result.append({"run_id": run["id"], "started_at": run["started_at"], "status": run["status"],
            "planned_pages": len(tasks), "completed_pages": sum(t["status"] == "complete" for t in tasks),
            "failed_pages": sum(t["status"] == "failed" for t in tasks), "accepted_rows": accepted,
            "quarantined_rows": rejected.get(run["id"], 0),
            "quality_rate": accepted / parsed if parsed else None, **counts})
    return pd.DataFrame(result)


def batch_details(engine, run_id, mode, limit=1000):
    with engine.connect() as connection:
        _begin_read(connection)
        run = connection.execute(select(db.runs.c.id).where(
            db.runs.c.id == run_id, db.runs.c.mode == mode)).first()
        if not run:
            raise ValueError("所选模式中不存在此批次")
        result = {}
        for name, table, order in [("tasks", db.tasks, [db.tasks.c.source, db.tasks.c.page]),
                ("observations", db.observations, [db.observations.c.source, db.observations.c.page, db.observations.c.record_key]),
                ("quarantine", db.quarantine, [db.quarantine.c.source, db.quarantine.c.page, db.quarantine.c.row_index]),
                ("events", db.events, [db.events.c.id.desc()])]:
            rows = [dict(row) for row in connection.execute(select(table).where(
                table.c.run_id == run_id).order_by(*order).limit(limit + 1)).mappings()]
            result[name + "_truncated"] = len(rows) > limit
            result[name] = rows[:limit]
    result["accepted"] = [{"source": row["source"], "page": row["page"], "outcome": row["outcome"],
                           **json.loads(row["payload_json"])} for row in result["observations"]]
    result["issues"] = dict(Counter(issue for row in result["quarantine"] for issue in json.loads(row["issues_json"])))
    return result
