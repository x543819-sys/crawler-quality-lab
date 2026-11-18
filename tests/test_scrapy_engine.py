from types import SimpleNamespace

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response
from scrapy.exceptions import IgnoreRequest

from crawler_lab.scrapy_engine import ProxyPool, ProxyMiddleware, interception, retry_delay


def response(body="<html><title>Book</title></html>", status=200, headers=None):
    return HtmlResponse("https://books.toscrape.com/", body=body.encode(), encoding="utf-8", status=status, headers=headers or {})


def test_proxy_round_robin_cooldown_and_exhaustion():
    clock = [0]
    a, b = "http://127.0.0.1:19081", "http://127.0.0.1:19082"
    pool = ProxyPool([a, b], clock=lambda: clock[0])
    assert pool.choose() == a
    pool.fail(a)
    assert pool.choose() == b
    pool.fail(b)
    with pytest.raises(IgnoreRequest):
        pool.choose()
    clock[0] = 61
    assert pool.choose() in (a, b)


@pytest.mark.parametrize("url", ["socks5://host:8080", "http://user:secret@host:8080", "http://host", "http://host:80/path"])
def test_proxy_config_rejects_unsupported_or_credential_urls(url):
    with pytest.raises(ValueError):
        ProxyPool([url])


def test_proxy_retry_replaces_old_proxy_and_removes_auth():
    a, b = "http://127.0.0.1:19081", "http://127.0.0.1:19082"
    pool = ProxyPool([a, b])
    middleware = ProxyMiddleware()
    middleware.crawler = SimpleNamespace(spider=SimpleNamespace(pool=pool, blocked=False, event=lambda *args: None))
    request = Request("https://books.toscrape.com/", meta={"cq_page": 1, "proxy": a, "_auth_proxy": a},
                      headers={"Proxy-Authorization": "old-secret"})
    pool.fail(a)
    middleware.process_request(request)
    assert request.meta["proxy"] == b
    assert "Proxy-Authorization" not in request.headers
    assert "_auth_proxy" not in request.meta


@pytest.mark.parametrize("body", ["<title>CAPTCHA</title>", '<input name="captcha">', '<div class="g-recaptcha"></div>'])
def test_captcha_is_not_successful_content(body):
    assert interception(response(body)) == "captcha_manual_review"


def test_plain_word_captcha_in_book_text_is_not_interception():
    assert interception(response("<title>Books</title><p>A book about CAPTCHA research</p>")) == ""
    assert interception(Response("https://books.toscrape.com/", status=403)) == "access_restricted"


def test_retry_after_not_silently_shortened():
    assert retry_delay(response(headers={"Retry-After": "120"}), 0) == 120
    assert retry_delay(response(headers={"Retry-After": "3"}), 0) == 3
    assert retry_delay(response(), 2) == 4
