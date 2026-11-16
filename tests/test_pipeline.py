import argparse
import json
import shutil
from pathlib import Path

import pytest
import requests

import run_project
from crawler_lab import storage as db
from crawler_lab.fetchers import DemoFetcher, FetchError, LiveFetcher, Retryable, retry_call
from crawler_lab.parsers import ParseError, parse_books, parse_quotes, validate
from crawler_lab.report import export_report, spreadsheet_safe

ROOT = Path(__file__).resolve().parents[1]
BOOK_URL = "https://books.toscrape.com/catalogue/page-1.html"


@pytest.fixture
def store(tmp_path):
    value = db.Store("sqlite:///" + (tmp_path / "test.sqlite").as_posix())
    yield value
    value.dispose()


def book():
    return parse_books((ROOT / "fixtures/books_1.html").read_text(encoding="utf-8"), BOOK_URL)[0]


def start(store, name, mode="demo", pages=1):
    store.start(name, {"mode": mode, "book_pages": pages, "quote_pages": 0, "fault": "none"},
                [("books", p) for p in range(1, pages + 1)])


def save(store, run_id, row, page=1):
    return store.save_page(run_id, "demo", "books", page, "snapshot.html", [(row, validate("books", row))])


def test_book_xpath_currency_rating_and_relative_link():
    row = book()
    assert row["title"] == "模拟图书 A"
    assert row["price"] == "12.50" and row["currency"] == "GBP"
    assert row["rating"] == 3
    assert row["url"] == "https://books.toscrape.com/catalogue/demo-book-a/index.html"
    assert validate("books", row) == []


@pytest.mark.parametrize("parser", [parse_books, parse_quotes])
def test_changed_selector_is_failure_not_zero_success(parser):
    with pytest.raises(ParseError):
        parser("<html><p>New layout, HTTP 200</p></html>", BOOK_URL)


def test_quote_key_stable_across_pages():
    content = (ROOT / "fixtures/quotes_1.html").read_text(encoding="utf-8")
    a = parse_quotes(content, "https://quotes.toscrape.com/js/page/1/")[0]
    b = parse_quotes(content, "https://quotes.toscrape.com/js/page/2/")[0]
    assert a["external_id"] == b["external_id"]
    assert validate("quotes", a) == []


@pytest.mark.parametrize("field,value,issue", [
    ("title", "  ", "missing_title"), ("price", "-1", "invalid_price"),
    ("price", "NaN", "invalid_price"), ("price", "bad", "invalid_price"),
    ("rating", 6, "invalid_rating"), ("url", "https://example.com/x", "unexpected_url_host")])
def test_bad_fields_quarantined(field, value, issue):
    row = book()
    row[field] = value
    assert issue in validate("books", row)


def test_quoted_text_requires_author():
    row = parse_quotes('<div class="quote"><span class="text">abc</span></div>', BOOK_URL)[0]
    assert "missing_author" in validate("quotes", row)


def test_repeat_runs_do_not_duplicate_and_change_is_versioned(store):
    row = book()
    start(store, "one")
    assert save(store, "one", row)["new"] == 1
    start(store, "two")
    assert save(store, "two", row)["unchanged"] == 1
    row["price"] = "13.50"
    row["price_raw"] = "£13.50"
    start(store, "three")
    assert save(store, "three", row)["updated"] == 1
    assert len(store.rows(db.records)) == 1
    assert len(store.rows(db.versions)) == 2


def test_duplicate_in_same_run_goes_to_quarantine(store):
    start(store, "duplicate", pages=2)
    row = book()
    save(store, "duplicate", row)
    stats = save(store, "duplicate", row, page=2)
    assert stats["quarantined"] == 1
    assert len(store.rows(db.observations)) == 1
    assert "duplicate_in_same_run" in store.rows(db.quarantine)[0]["issues_json"]


def test_invalid_update_does_not_overwrite_good_record(store):
    start(store, "good")
    save(store, "good", book())
    broken = book()
    broken["title"] = ""
    start(store, "bad")
    assert save(store, "bad", broken)["quarantined"] == 1
    assert json.loads(store.rows(db.records)[0]["payload_json"])["title"] == "模拟图书 A"


def test_record_and_checkpoint_transaction_roll_back_together(store):
    start(store, "atomic")
    invalid = {**book(), "external_id": "different", "unserializable": object()}
    with pytest.raises(TypeError):
        store.save_page("atomic", "demo", "books", 1, "x", [(book(), []), (invalid, [])])
    assert not store.rows(db.records)
    assert not store.rows(db.observations)
    assert store.rows(db.tasks)[0]["status"] == "pending"


def test_demo_and_live_keys_cannot_mix(store):
    start(store, "demo")
    save(store, "demo", book())
    start(store, "live", mode="live")
    store.save_page("live", "live", "books", 1, "x", [(book(), [])])
    assert len(store.rows(db.records)) == 2


def test_retry_transient_and_permanent():
    calls, events = [], []
    def transient():
        calls.append(1)
        if len(calls) < 3:
            raise Retryable("503")
        return "ok"
    assert retry_call(transient, lambda k, d: events.append(k), sleep=lambda n: None) == "ok"
    assert len(calls) == 3 and events.count("retry") == 2
    calls.clear()
    def permanent():
        calls.append(1)
        raise FetchError("404")
    with pytest.raises(FetchError):
        retry_call(permanent, lambda *args: None, sleep=lambda n: None)
    assert len(calls) == 1


def test_retry_after_not_shortened():
    sleeps = []
    with pytest.raises(FetchError, match="较长等待"):
        retry_call(lambda: (_ for _ in ()).throw(Retryable("429", wait=120)),
                   lambda *args: None, sleep=sleeps.append)
    assert sleeps == []


def test_requests_404_not_retried(tmp_path, monkeypatch):
    fetcher = LiveFetcher(tmp_path, lambda *args: None)
    response = requests.Response()
    response.status_code = 404
    response._content = b"missing"
    calls = []
    monkeypatch.setattr(fetcher, "policy", lambda url: 0)
    monkeypatch.setattr(fetcher, "throttle", lambda *args: None)
    def get(*args, **kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(fetcher.session, "get", get)
    with pytest.raises(FetchError, match="404"):
        fetcher.get("books", 1, BOOK_URL)
    assert len(calls) == 1
    fetcher.close()


@pytest.mark.parametrize("status", [403, 500])
def test_robots_failure_stops_fetch(tmp_path, monkeypatch, status):
    fetcher = LiveFetcher(tmp_path, lambda *args: None)
    response = requests.Response()
    response.status_code = status
    response._content = b"failure"
    monkeypatch.setattr(fetcher, "request", lambda url: response)
    with pytest.raises(FetchError, match="robots"):
        fetcher.policy(BOOK_URL)
    fetcher.close()


def test_robots_disallow_stops_fetch(tmp_path, monkeypatch):
    fetcher = LiveFetcher(tmp_path, lambda *args: None)
    response = requests.Response()
    response.status_code = 200
    response._content = b"User-agent: *\nDisallow: /catalogue/\n"
    response.encoding = "utf-8"
    monkeypatch.setattr(fetcher, "request", lambda url: response)
    with pytest.raises(FetchError, match="不允许"):
        fetcher.policy(BOOK_URL)
    fetcher.close()


def setup_project(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "fixtures", tmp_path / "fixtures")
    monkeypatch.setattr(run_project, "ROOT", tmp_path)
    return argparse.Namespace(mode="demo", book_pages=2, quote_pages=2, fault="none", resume=None,
                              stop_after_pages=0, open_report=False)


def test_checkpoint_resume_does_not_repeat_committed_page(tmp_path, monkeypatch, store):
    args = setup_project(tmp_path, monkeypatch)
    args.stop_after_pages = 1
    first = run_project.execute(args, store)
    assert first["status"] == "interrupted" and first["accepted_rows"] == 2
    args.resume, args.stop_after_pages = first["run_id"], 0
    second = run_project.execute(args, store)
    assert second["status"] == "complete" and second["accepted_rows"] == 6
    assert second["quarantined_rows"] == 0
    assert len(store.rows(db.records)) == 6


def test_selector_failure_can_resume_after_parser_recovered(tmp_path, monkeypatch, store):
    args = setup_project(tmp_path, monkeypatch)
    args.fault = "selector"
    first = run_project.execute(args, store)
    assert first["status"] == "partial" and first["failed_pages"] == 1
    args.resume = first["run_id"]
    second = run_project.execute(args, store, fetcher_factory=lambda log: DemoFetcher(tmp_path, log, "none"))
    assert second["status"] == "complete" and second["accepted_rows"] == 6
    assert second["quarantined_rows"] == 0


def test_dirty_run_conservation_and_report_provenance(tmp_path, monkeypatch, store):
    args = setup_project(tmp_path, monkeypatch)
    args.fault = "dirty"
    stats = run_project.execute(args, store)
    assert stats["status"] == "partial"
    assert (stats["parsed_rows"], stats["accepted_rows"], stats["quarantined_rows"]) == (8, 6, 2)
    report = tmp_path / "runs" / stats["run_id"] / "report.html"
    assert "模拟数据演示" in report.read_text(encoding="utf-8")
    assert stats["quality_acceptance_rate"] == .75


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-cmd", "@sum(A1)", "  =SUM(A1)"])
def test_csv_external_text_not_formula(value):
    assert spreadsheet_safe(value).startswith("'")


def test_zero_accepted_has_no_misleading_quality_rate(store, tmp_path):
    start(store, "zero")
    store.failed("zero", "books", 1, "HTTP 500")
    store.finish("zero", "failed")
    stats = export_report(store, "zero", tmp_path / "zero")
    assert stats["quality_acceptance_rate"] is None
    assert stats["status"] == "failed"
