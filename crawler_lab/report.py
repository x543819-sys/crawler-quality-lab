"""Self-contained local report; original snapshots and JSON retain exact text."""
import html
import json
from collections import Counter
from datetime import datetime

import pandas as pd

from . import storage as db


def spreadsheet_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def save_csv(rows, path, fields=None):
    frame = pd.DataFrame(rows, columns=fields) if fields is not None else pd.DataFrame(rows)
    if frame.empty and not len(frame.columns):
        frame = pd.DataFrame(columns=["note"])
    frame.map(spreadsheet_safe).to_csv(path, index=False, encoding="utf-8-sig")
    return frame


def export_report(store, run_id, folder):
    folder.mkdir(parents=True, exist_ok=True)
    run = store.rows(db.runs, db.runs.c.id == run_id)[0]
    config = json.loads(run["config_json"])
    task_rows = sorted(store.rows(db.tasks, db.tasks.c.run_id == run_id), key=lambda r: (r["source"], r["page"]))
    observed = store.rows(db.observations, db.observations.c.run_id == run_id)
    rejected = store.rows(db.quarantine, db.quarantine.c.run_id == run_id)
    event_rows = sorted(store.rows(db.events, db.events.c.run_id == run_id), key=lambda r: r["id"])
    outcomes = Counter(r["outcome"] for r in observed)
    parsed_count = sum(r["row_count"] or 0 for r in task_rows)
    issue_counts = Counter(issue for row in rejected for issue in json.loads(row["issues_json"]))
    stats = {
        "run_id": run_id, "mode": run["mode"], "fault": config.get("fault", "none"),
        "backend": store.backend, "status": run["status"],
        "started_at_utc": run["started_at"], "finished_at_utc": run["finished_at"],
        "page_limits": {"books": config["book_pages"], "quotes": config["quote_pages"]},
        "planned_pages": len(task_rows), "completed_pages": sum(r["status"] == "complete" for r in task_rows),
        "failed_pages": sum(r["status"] == "failed" for r in task_rows),
        "pending_pages": sum(r["status"] == "pending" for r in task_rows),
        "parsed_rows": parsed_count, "accepted_rows": len(observed), "quarantined_rows": len(rejected),
        "new_records": outcomes["new"], "updated_records": outcomes["updated"], "unchanged_records": outcomes["unchanged"],
        "retry_events": sum(r["kind"] == "retry" for r in event_rows),
        "page_fetch_attempt_events": sum(r["kind"] == "fetch_attempt" for r in event_rows),
        "quality_acceptance_rate": len(observed) / parsed_count if parsed_count else None,
        "issues": dict(issue_counts),
        "scope_note": "只统计配置指定的前N页；图书价格与评分是练习站演示数据；页面失败不计作0条成功。",
    }
    if parsed_count != len(observed) + len(rejected):
        raise RuntimeError("记录数量不守恒：禁止导出看似健康的报告")
    accepted = []
    for row in observed:
        accepted.append({"run_id": run_id, "mode": run["mode"], "source": row["source"],
                         "page": row["page"], "outcome": row["outcome"], **json.loads(row["payload_json"])})
    bad_rows = [{"source": r["source"], "page": r["page"], "row_index": r["row_index"],
                 "issues": ";".join(json.loads(r["issues_json"])), "raw_record": r["payload_json"]} for r in rejected]
    fields = {
        "books": ["run_id", "mode", "source", "page", "outcome", "external_id", "title", "price", "price_raw", "currency", "rating", "availability", "url"],
        "quotes": ["run_id", "mode", "source", "page", "outcome", "external_id", "text", "author", "tags", "url"],
    }
    source_frames = {}
    for source in ["books", "quotes"]:
        source_frames[source] = save_csv([r for r in accepted if r["source"] == source], folder / f"{source}.csv", fields[source])
    save_csv(bad_rows, folder / "quarantine.csv", ["source", "page", "row_index", "issues", "raw_record"])
    save_csv(task_rows, folder / "tasks.csv")
    save_csv(event_rows, folder / "events.csv")
    (folder / "summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "accepted.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in accepted), encoding="utf-8")
    (folder / "events.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in event_rows), encoding="utf-8")
    source_summary = []
    for source in ["books", "quotes"]:
        subset = [r for r in task_rows if r["source"] == source]
        source_summary.append({"来源": source, "计划页": len(subset), "完成页": sum(r["status"] == "complete" for r in subset),
            "通过记录": sum(r["source"] == source for r in observed), "隔离记录": sum(r["source"] == source for r in rejected)})
    rate = "不可计算" if stats["quality_acceptance_rate"] is None else f"{stats['quality_acceptance_rate']:.1%}"
    mode_name = "真实联网采集" if run["mode"] == "live" else "模拟数据演示"
    parts = [f"<p class='eyebrow'>{html.escape(mode_name)} / {html.escape(store.backend)}</p>",
             "<h1>多源采集与数据质量报告</h1>",
             f"<p>批次 {html.escape(run_id)} · 状态 <strong>{html.escape(run['status'])}</strong></p>",
             f"<p class='note'>{html.escape(stats['scope_note'])}</p>",
             "<div class='metrics'>" + "".join(f"<div><span>{label}</span><b>{value}</b></div>" for label, value in [
                 ("完成 / 计划页", f"{stats['completed_pages']} / {len(task_rows)}"), ("通过记录", len(observed)),
                 ("隔离记录", len(rejected)), ("已解析记录通过率", rate)]) + "</div>",
             "<h2>来源与增量</h2>", pd.DataFrame(source_summary).to_html(index=False, border=0),
             f"<p>新增 {outcomes['new']} · 内容变化 {outcomes['updated']} · 内容未变 {outcomes['unchanged']} · 重试事件 {stats['retry_events']}</p>",
             "<h2>任务状态</h2>", pd.DataFrame(task_rows)[["source", "page", "status", "row_count", "error"]].to_html(index=False, border=0, escape=True),
             "<h2>质量问题</h2>",
             (pd.DataFrame([{"问题": k, "涉及记录数": v} for k, v in issue_counts.items()], columns=["问题", "涉及记录数"]).to_html(index=False, border=0)
              if issue_counts else "<p>本批次未发现所配置的记录级质量问题。</p>"),
             "<p>一个样本可能包含多个问题。未发现上述问题不等于完成全部内容真实性核验。</p>"]
    for source in ["books", "quotes"]:
        cols = ["title", "price", "rating"] if source == "books" else ["text", "author"]
        parts.extend([f"<h2>{source} 样本（最多8条）</h2>", source_frames[source][cols].head(8).to_html(index=False, border=0, escape=True)])
    parts.append("<h2>结果文件</h2><p>同目录包含 books.csv、quotes.csv、quarantine.csv、tasks.csv、summary.json 和 events.jsonl。CSV 对可能被Excel误当成公式的外部文本添加前导单引号；accepted.jsonl 保留原始字段文本。</p>")
    style = """body{font:16px/1.65 'Microsoft YaHei',sans-serif;color:#1e293b;background:#f3f6fa;margin:0}main{max-width:1080px;margin:32px auto;padding:32px;background:white;border-radius:16px}h1{font-size:30px;margin:0}h2{margin-top:30px;font-size:21px}.eyebrow{color:#087f73;font-weight:bold}.note{background:#eef5f8;padding:14px;border-left:4px solid #087f73}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}.metrics div{background:#edf7f4;padding:18px;border-radius:10px}.metrics span{display:block;color:#526173}.metrics b{font-size:28px}table{border-collapse:collapse;width:100%;font-size:14px;table-layout:auto}th,td{text-align:left;border-bottom:1px solid #dde5eb;padding:10px;overflow-wrap:anywhere}th{background:#edf1f7}td{max-width:500px}@media(max-width:700px){main{margin:0;padding:16px}.metrics{grid-template-columns:repeat(2,1fr)}}"""
    (folder / "report.html").write_text("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>采集与质量报告</title><style>" + style + "</style><main>" + "\n".join(parts) + "</main></html>", encoding="utf-8")
    return stats
