"""Bounded Scrapy book spider, proxy health and interception handling."""
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import scrapy
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse

from .parsers import parse_books, validate


class ProxyPool:
    def __init__(self, urls=(), cooldown=60, clock=time.monotonic):
        self.urls, self.until, self.index = list(urls), {}, 0
        self.cooldown, self.clock = cooldown, clock
        for url in self.urls:
            parts = urlsplit(url)
            if (parts.scheme != "http" or not parts.hostname or not parts.port
                    or parts.username or parts.password or parts.path not in ("", "/")
                    or parts.query or parts.fragment):
                raise ValueError("代理须为无账号的http://主机:端口；本练习不接收含凭据的代理URL")

    def choose(self):
        if not self.urls:
            return None
        for _ in self.urls:
            url = self.urls[self.index % len(self.urls)]
            self.index += 1
            if self.until.get(url, 0) <= self.clock():
                return url
        raise IgnoreRequest("proxy_pool_exhausted: 当前代理均在冷却，停止本次请求")

    def fail(self, url):
        self.until[url] = self.clock() + self.cooldown

    def label(self, url):
        return "proxy_" + str(self.urls.index(url) + 1)


def interception(response):
    if not isinstance(response, HtmlResponse):
        return "access_restricted" if response.status in (401, 403) else ""
    markers = response.css(".g-recaptcha, .h-captcha, #challenge-form, input[name='captcha'], iframe[src*='recaptcha'], form[action*='captcha']")
    title = " ".join(response.css("title::text").getall()).lower()
    if markers or any(word in title for word in ("captcha", "verify you are human", "人机验证", "验证码")):
        return "captcha_manual_review"
    return "access_restricted" if response.status in (401, 403) else ""


def retry_delay(response, retries):
    value = response.headers.get("Retry-After")
    if value:
        raw = value.decode("ascii", errors="ignore").strip()
        try:
            delay = int(raw)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return min(2 ** retries, 30)
        return max(0, delay)
    return min(2 ** retries, 30)


class ProxyMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_request(self, request):
        spider = self.crawler.spider
        if spider.blocked:
            raise IgnoreRequest("batch_paused_for_review")
        # A retried request must not retain the failed proxy or its authentication header.
        request.headers.pop("Proxy-Authorization", None)
        request.meta.pop("_auth_proxy", None)
        proxy = spider.pool.choose()
        request.meta["proxy"] = proxy
        if request.meta.get("cq_page"):
            spider.event(request, "fetch_attempt", "scrapy")
        if proxy and request.meta.get("cq_page"):
            spider.event(request, "proxy_selected", spider.pool.label(proxy))

    def process_exception(self, request, exception):
        spider = self.crawler.spider
        proxy = request.meta.get("proxy")
        # IgnoreRequest also represents deliberate pause/exhaustion, not a faulty proxy.
        if proxy and not isinstance(exception, IgnoreRequest):
            spider.pool.fail(proxy)
            spider.event(request, "proxy_failure", spider.pool.label(proxy) + ":" + type(exception).__name__)

    def process_response(self, request, response):
        spider = self.crawler.spider
        if response.status == 407 and request.meta.get("proxy"):
            spider.pool.fail(request.meta["proxy"])
            spider.event(request, "proxy_failure", "proxy_auth_rejected")
        return response


class GuardMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_response(self, request, response):
        spider = self.crawler.spider
        if request.meta.get("cq_page"):
            reason = interception(response)
            if reason:
                spider.pause(request, response, reason)
                raise IgnoreRequest(reason)
        return response


class BoundedRetryMiddleware(RetryMiddleware):
    def process_response(self, request, response):
        if response.status in self.retry_http_codes and not request.meta.get("dont_retry"):
            delay = retry_delay(response, request.meta.get("retry_times", 0))
            spider = self.crawler.spider
            if delay > 30:
                spider.pause(request, response, "retry_after_exceeds_budget")
                raise IgnoreRequest("retry_after_exceeds_budget")
            retry = self._retry(request, response.status)
            if retry:
                spider.event(request, "retry", f"HTTP {response.status}; wait={delay}s")
                from twisted.internet import reactor
                from twisted.internet.task import deferLater
                return deferLater(reactor, delay, lambda: retry)
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, IgnoreRequest):
            return None
        result = super().process_exception(request, exception)
        if result:
            self.crawler.spider.event(request, "retry", type(exception).__name__)
        return result


class DemoDownloader:
    """Deliberate simulated transport; no proxy endpoints or websites are contacted."""
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        instance.failed_once = False
        return instance

    def process_request(self, request):
        spider = self.crawler.spider
        if spider.mode != "demo" or not request.meta.get("cq_page"):
            return None
        page = request.meta["cq_page"]
        if spider.scenario == "proxy-failure" and not self.failed_once:
            self.failed_once = True
            from twisted.internet.error import ConnectionRefusedError
            raise ConnectionRefusedError("simulated_proxy_failure")
        if spider.scenario == "captcha" and page == 2:
            body = '<html><title>Verify you are human</title><form action="/captcha"><input name="captcha"></form></html>'
        elif spider.scenario == "rate-limit" and page == 2:
            return HtmlResponse(request.url, status=429, headers={"Retry-After": "120"}, body=b"rate limited", request=request)
        else:
            body = (spider.root / f"fixtures/books_{page}.html").read_text(encoding="utf-8")
        return HtmlResponse(request.url, body=body.encode("utf-8"), encoding="utf-8", request=request)


class PageItem(scrapy.Item):
    page = scrapy.Field()
    document = scrapy.Field()
    url = scrapy.Field()


class PagePipeline:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_item(self, item):
        spider = self.crawler.spider
        page = item["page"]
        snapshot = spider.folder / "snapshots" / f"books_{page}.html"
        snapshot.write_text(item["document"], encoding="utf-8")
        try:
            rows = parse_books(item["document"], item["url"])
            counts = spider.store.save_page(spider.run_id, spider.mode, "books", page,
                str(snapshot.relative_to(spider.root)), [(row, validate("books", row)) for row in rows])
            spider.store.log(spider.run_id, "books", page, "page_committed", json.dumps(counts))
        except Exception as error:
            spider.store.failed(spider.run_id, "books", page, type(error).__name__, str(snapshot.relative_to(spider.root)))
        return item


class BooksSpider(scrapy.Spider):
    name = "quality_books"
    allowed_domains = ["books.toscrape.com"]

    def __init__(self, root, folder, store, run_id, mode, scenario, pages, pool, **kwargs):
        super().__init__(**kwargs)
        self.root, self.folder, self.store, self.run_id = root, folder, store, run_id
        self.mode, self.scenario, self.pages, self.pool = mode, scenario, pages, pool
        self.blocked = False

    async def start(self):
        for page in self.pages:
            yield scrapy.Request(f"https://books.toscrape.com/catalogue/page-{page}.html",
                callback=self.parse, errback=self.page_error, meta={"cq_page": page})

    def event(self, request, kind, detail):
        page = request.meta.get("cq_page")
        if page:
            self.store.log(self.run_id, "books", page, kind, detail)

    def pause(self, request, response, reason):
        self.blocked = True
        page = request.meta["cq_page"]
        snapshot = self.folder / "snapshots" / f"books_{page}.html"
        snapshot.write_bytes(response.body)
        self.store.failed(self.run_id, "books", page, reason, str(snapshot.relative_to(self.root)))
        request.meta["cq_failure_saved"] = True
        self.event(request, "manual_review_required", reason)
        from scrapy.utils.defer import deferred_from_coro
        deferred_from_coro(self.crawler.engine.close_spider_async(reason=reason))

    def parse(self, response):
        self.event(response.request, "fetch_completed", str(response.status))
        yield PageItem(page=response.meta["cq_page"], document=response.text, url=response.url)

    def page_error(self, failure):
        request = failure.request
        if not request.meta.get("cq_failure_saved"):
            self.store.failed(self.run_id, "books", request.meta["cq_page"], type(failure.value).__name__)


def settings(mode):
    return {"ROBOTSTXT_OBEY": mode == "live", "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 1 if mode == "live" else 0, "DOWNLOAD_TIMEOUT": 15,
        "AUTOTHROTTLE_ENABLED": mode == "live", "AUTOTHROTTLE_START_DELAY": 1,
        "AUTOTHROTTLE_MAX_DELAY": 15, "AUTOTHROTTLE_TARGET_CONCURRENCY": 1,
        "RETRY_TIMES": 2, "RETRY_HTTP_CODES": [407, 408, 429, 500, 502, 503, 504],
        "USER_AGENT": "CrawlerQualityLab/0.3 (bounded practice-site collector)",
        "COOKIES_ENABLED": False, "LOG_LEVEL": "WARNING", "TELNETCONSOLE_ENABLED": False,
        "DOWNLOADER_MIDDLEWARE_RESPONSE_EXCEPTIONS": True,
        "DOWNLOADER_MIDDLEWARES": {"scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
            "crawler_lab.scrapy_engine.BoundedRetryMiddleware": 550,
            "crawler_lab.scrapy_engine.GuardMiddleware": 600,
            "crawler_lab.scrapy_engine.ProxyMiddleware": 740,
            "crawler_lab.scrapy_engine.DemoDownloader": 760},
        "ITEM_PIPELINES": {"crawler_lab.scrapy_engine.PagePipeline": 300}}
