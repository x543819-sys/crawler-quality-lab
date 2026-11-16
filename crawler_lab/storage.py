"""Portable SQLAlchemy schema; tested SQLite and MySQL use the same operations.

One worker per database is supported. This is deliberately not a distributed queue.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (Column, Integer, MetaData, String, Table, Text, and_,
                        create_engine, insert, select, update)

from .parsers import digest


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


metadata = MetaData()
runs = Table("cq_runs", metadata,
    Column("id", String(40), primary_key=True), Column("mode", String(16), nullable=False),
    Column("config_json", Text, nullable=False), Column("status", String(20), nullable=False),
    Column("started_at", String(40)), Column("finished_at", String(40)))
tasks = Table("cq_tasks", metadata,
    Column("run_id", String(40), primary_key=True), Column("source", String(16), primary_key=True),
    Column("page", Integer, primary_key=True), Column("status", String(20)),
    Column("snapshot", Text), Column("error", Text), Column("row_count", Integer, default=0))
records = Table("cq_records", metadata,
    Column("record_key", String(64), primary_key=True), Column("mode", String(16), nullable=False),
    Column("source", String(16), nullable=False), Column("external_id", Text),
    Column("payload_json", Text, nullable=False), Column("payload_hash", String(64)),
    Column("first_seen", String(40)), Column("last_seen", String(40)))
observations = Table("cq_observations", metadata,
    Column("run_id", String(40), primary_key=True), Column("record_key", String(64), primary_key=True),
    Column("source", String(16)), Column("page", Integer), Column("outcome", String(16)),
    Column("payload_json", Text, nullable=False))
versions = Table("cq_versions", metadata,
    Column("record_key", String(64), primary_key=True), Column("payload_hash", String(64), primary_key=True),
    Column("payload_json", Text), Column("first_run_id", String(40)))
quarantine = Table("cq_quarantine", metadata,
    Column("run_id", String(40), primary_key=True), Column("source", String(16), primary_key=True),
    Column("page", Integer, primary_key=True), Column("row_index", Integer, primary_key=True),
    Column("payload_json", Text), Column("issues_json", Text))
events = Table("cq_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("run_id", String(40)),
    Column("at", String(40)), Column("source", String(16)), Column("page", Integer),
    Column("kind", String(40)), Column("detail", Text))


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        metadata.create_all(self.engine)

    @property
    def backend(self):
        return self.engine.dialect.name

    def rows(self, table, condition=None):
        statement = select(table)
        if condition is not None:
            statement = statement.where(condition)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(statement).mappings()]

    def start(self, run_id, config, plan):
        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(id=run_id, mode=config["mode"], config_json=encode(config),
                                            status="running", started_at=now()))
            conn.execute(insert(tasks), [{"run_id": run_id, "source": s, "page": p, "status": "pending",
                                          "error": "", "snapshot": "", "row_count": 0} for s, p in plan])

    def task_condition(self, run_id, source, page):
        return and_(tasks.c.run_id == run_id, tasks.c.source == source, tasks.c.page == page)

    def log(self, run_id, source, page, kind, detail):
        with self.engine.begin() as conn:
            conn.execute(insert(events).values(run_id=run_id, at=now(), source=source,
                                               page=page, kind=kind, detail=str(detail)[:4000]))

    def failed(self, run_id, source, page, error, snapshot=""):
        with self.engine.begin() as conn:
            conn.execute(update(tasks).where(self.task_condition(run_id, source, page)).values(
                status="failed", error=str(error)[:4000], snapshot=snapshot))
        self.log(run_id, source, page, "page_failed", error)

    def save_page(self, run_id, mode, source, page, snapshot, batch):
        """Records, observations, quarantine and checkpoint commit atomically."""
        counts = {"new": 0, "updated": 0, "unchanged": 0, "quarantined": 0}
        with self.engine.begin() as conn:
            for index, (row, issues) in enumerate(batch):
                key = digest(mode + "\n" + source + "\n" + row["external_id"])
                seen = conn.execute(select(observations.c.record_key).where(and_(
                    observations.c.run_id == run_id, observations.c.record_key == key))).first()
                issues = list(issues)
                if not issues and seen:
                    issues.append("duplicate_in_same_run")
                if issues:
                    conn.execute(insert(quarantine).values(run_id=run_id, source=source, page=page,
                        row_index=index, payload_json=encode(row), issues_json=encode(issues)))
                    counts["quarantined"] += 1
                    continue
                payload = encode(row)
                fingerprint = digest(payload)
                old = conn.execute(select(records).where(records.c.record_key == key)).mappings().first()
                outcome = "new" if old is None else ("unchanged" if old["payload_hash"] == fingerprint else "updated")
                values = dict(mode=mode, source=source, external_id=row["external_id"],
                              payload_json=payload, payload_hash=fingerprint, last_seen=now())
                if old is None:
                    conn.execute(insert(records).values(record_key=key, first_seen=now(), **values))
                else:
                    conn.execute(update(records).where(records.c.record_key == key).values(**values))
                if not conn.execute(select(versions.c.record_key).where(and_(
                    versions.c.record_key == key, versions.c.payload_hash == fingerprint))).first():
                    conn.execute(insert(versions).values(record_key=key, payload_hash=fingerprint,
                        payload_json=payload, first_run_id=run_id))
                conn.execute(insert(observations).values(run_id=run_id, record_key=key, source=source,
                    page=page, outcome=outcome, payload_json=payload))
                counts[outcome] += 1
            conn.execute(update(tasks).where(self.task_condition(run_id, source, page)).values(
                status="complete", snapshot=snapshot, error="", row_count=len(batch)))
        return counts

    def finish(self, run_id, status):
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == run_id).values(status=status, finished_at=now()))

    def dispose(self):
        self.engine.dispose()
