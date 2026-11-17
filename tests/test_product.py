import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from streamlit.testing.v1 import AppTest

from crawler_lab import analytics, storage as db
from crawler_lab.parsers import parse_books, validate
from crawler_lab.scheduling import ScheduledCrawler, validate_schedule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reader(tmp_path):
    (tmp_path / "data").mkdir()
    store = db.Store("sqlite:///" + (tmp_path / "data/crawler.sqlite").as_posix())
    row = parse_books((ROOT / "fixtures/books_1.html").read_text(encoding="utf-8"),
                      "https://books.toscrape.com/catalogue/page-1.html")[0]
    for run_id, mode in [("demo_ok", "demo"), ("demo_bad", "demo"), ("live_only", "live")]:
        store.start(run_id, {"mode": mode, "book_pages": 1, "quote_pages": 0}, [("books", 1)])
        if run_id == "demo_bad":
            store.failed(run_id, "books", 1, "ParseError: empty selector")
            store.finish(run_id, "failed")
        else:
            store.save_page(run_id, mode, "books", 1, "", [(row, validate("books", row))])
            store.finish(run_id, "complete")
    store.dispose()
    engine = analytics.reader_engine(tmp_path, "sqlite")
    yield engine
    engine.dispose()


def test_dashboard_is_read_only_and_modes_do_not_mix(reader):
    frame = analytics.history(reader, "demo")
    assert set(frame.run_id) == {"demo_ok", "demo_bad"}
    assert frame.set_index("run_id").loc["demo_ok", "accepted_rows"] == 1
    assert frame.set_index("run_id").loc["demo_bad", "failed_pages"] == 1
    assert frame.set_index("run_id").loc["demo_bad", "quality_rate"] != frame.set_index("run_id").loc["demo_bad", "quality_rate"]
    with reader.connect() as connection, pytest.raises(OperationalError):
        connection.execute(text("DELETE FROM cq_runs"))
    with pytest.raises(ValueError):
        analytics.batch_details(reader, "live_only", "demo")


def test_empty_history_and_missing_database_do_not_create_files(tmp_path, reader):
    assert analytics.history(reader, "missing").empty
    with pytest.raises(FileNotFoundError):
        analytics.reader_engine(tmp_path / "absent", "sqlite")
    assert not (tmp_path / "absent").exists()


def test_detail_limit_is_explicit(reader):
    details = analytics.batch_details(reader, "demo_ok", "demo", limit=0)
    assert details["observations"] == []
    assert details["observations_truncated"]


def test_scheduler_busy_does_not_launch_or_create_a_batch(tmp_path, monkeypatch):
    worker = ScheduledCrawler(tmp_path, "demo", "sqlite")
    def forbidden(*args, **kwargs):
        raise AssertionError("must not start a subprocess while worker is locked")
    monkeypatch.setattr("crawler_lab.scheduling.subprocess.run", forbidden)
    with FileLock(str(tmp_path / "data/worker.lock")):
        result = worker.run_once()
    assert result["status"] == "skipped_busy" and result["run_id"] is None
    assert len(list((tmp_path / "data/scheduler").glob("*.json"))) == 1


def test_scheduler_uses_existing_crawl_entry_and_persists_failure(tmp_path, monkeypatch):
    worker = ScheduledCrawler(tmp_path, "demo", "sqlite")
    def failed(command, **kwargs):
        assert "launcher.py" in command[2]
        assert "--open-report" not in command
        kwargs["stdout"].write("新批次： r20261117T000000000000Z_abcdef\n")
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr("crawler_lab.scheduling.subprocess.run", failed)
    result = worker.run_once()
    assert result["status"] == "partial_or_failed"
    assert result["run_id"] == "r20261117T000000000000Z_abcdef"
    saved = json.loads((worker.folder / (result["attempt_id"] + ".json")).read_text(encoding="utf-8"))
    assert saved["exit_code"] == 1


@pytest.mark.parametrize("mode,interval,pages", [("live", 899, 2), ("demo", 4, 2), ("demo", 30, 3)])
def test_invalid_schedule_rejected(mode, interval, pages):
    with pytest.raises(ValueError):
        validate_schedule(mode, "sqlite", interval, pages, 2)


def test_dashboard_filters_and_reruns_never_start_crawls(reader, monkeypatch):
    monkeypatch.setattr(analytics, "reader_engine", lambda *args: reader)
    def forbidden(*args, **kwargs):
        raise AssertionError("dashboard must not schedule or run collection")
    monkeypatch.setattr("crawler_lab.scheduling.subprocess.run", forbidden)
    app = AppTest.from_file(str(ROOT / "dashboard.py")).run(timeout=30)
    assert not app.exception and not app.error
    assert len(app.metric) == 4
    app.sidebar.radio[0].set_value("live").run(timeout=30)
    assert not app.exception and not app.error
    assert app.selectbox(key="batch").value == "live_only"
    app.sidebar.button[0].click().run(timeout=30)
    assert not app.exception and not app.error
    assert app.metric[1].value == "1"
