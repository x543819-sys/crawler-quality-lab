"""Requests for static HTML, isolated Chromium for JavaScript-rendered HTML."""
import random
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


class FetchError(RuntimeError):
    def __init__(self, message, document=""):
        super().__init__(message)
        self.document = document


class Retryable(FetchError):
    def __init__(self, message, document="", wait=None):
        super().__init__(message, document)
        self.wait = wait


def retry_call(operation, log, attempts=3, sleep=time.sleep):
    for attempt in range(1, attempts + 1):
        log("fetch_attempt", str(attempt))
        try:
            return operation()
        except (Retryable, requests.Timeout, requests.ConnectionError) as error:
            if attempt == attempts:
                raise
            wait = getattr(error, "wait", None)
            if wait is None:
                wait = 2 ** (attempt - 1) + random.uniform(0, .2)
            if wait > 30:
                raise FetchError("服务器要求较长等待；本轮停止，可稍后续采", getattr(error, "document", "")) from error
            log("retry", f"attempt={attempt};wait={wait:.2f};reason={error}")
            sleep(wait)


class LiveFetcher:
    def __init__(self, root, log, delay=1.0):
        self.root, self.log, self.delay = root, log, max(1.0, delay)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "CrawlerQualityLab/0.1 (educational collection)"
        self.last_request, self.policies, self.browser = {}, {}, None
        self.active_log = lambda kind, detail: None

    def throttle(self, host, delay=None):
        interval = max(self.delay, delay or 0)
        time.sleep(max(0, interval - (time.monotonic() - self.last_request.get(host, 0))))
        self.last_request[host] = time.monotonic()

    def request(self, url):
        host = urlparse(url).hostname
        self.throttle(host)
        response = self.session.get(url, timeout=(10, 25))
        response.encoding = "utf-8"
        if response.status_code in {429, 500, 502, 503, 504}:
            wait = None
            value = response.headers.get("Retry-After", "")
            if value:
                try:
                    wait = max(0, float(value))
                except ValueError:
                    try:
                        wait = max(0, parsedate_to_datetime(value).timestamp() - time.time())
                    except (ValueError, TypeError):
                        pass
            raise Retryable(f"HTTP {response.status_code}", response.text, wait)
        return response

    def policy(self, url):
        parsed = urlparse(url)
        host = parsed.hostname
        if host not in self.policies:
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            response = retry_call(lambda: self.request(robots_url), self.active_log)
            if response.status_code == 404:
                self.policies[host] = None
                self.active_log("robots_missing", "robots.txt返回404；该站为官方爬虫练习沙盒")
            elif response.status_code == 200:
                policy = RobotFileParser()
                policy.parse(response.text.splitlines())
                self.policies[host] = policy
                self.active_log("robots_checked", robots_url)
            else:
                raise FetchError(f"无法确认robots规则：HTTP {response.status_code}", response.text)
        policy = self.policies[host]
        if policy and not policy.can_fetch("CrawlerQualityLab", url):
            raise FetchError("robots.txt不允许该路径，停止该任务")
        return policy.crawl_delay("CrawlerQualityLab") or policy.crawl_delay("*") if policy else 0

    def start_browser(self):
        from DrissionPage import Chromium, ChromiumOptions
        paths = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                 Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")]
        browser_path = next((p for p in paths if p.is_file()), None)
        if browser_path is None:
            raise FetchError("未找到Edge或Chrome；动态采集需要Chromium浏览器")
        options = ChromiumOptions(read_file=False).set_browser_path(str(browser_path))
        # auto_port allocates a fresh browser and temporary profile; never attach
        # to a user's logged-in browser. 4.1 uses auto_port(on_off, scope).
        options.auto_port().headless()
        options.set_argument("--no-first-run")
        self.browser = Chromium(options)
        return self.browser.latest_tab

    def get(self, source, page, url):
        self.active_log = lambda kind, detail: self.log(source, page, kind, detail)
        crawl_delay = self.policy(url)

        def operation():
            if source == "books":
                response = self.request(url)
                if response.status_code != 200:
                    raise FetchError(f"HTTP {response.status_code}，不盲目重试", response.text)
                return response.text
            self.throttle(urlparse(url).hostname, crawl_delay)
            tab = self.browser.latest_tab if self.browser else self.start_browser()
            if tab.get(url, retry=0, timeout=25) is False:
                raise Retryable("browser_navigation_failed", tab.html)
            deadline = time.monotonic() + 15
            document = ""
            while time.monotonic() < deadline:
                document = tab.html
                if BeautifulSoup(document, "html.parser").select_one("div.quote"):
                    return document
                time.sleep(.25)
            raise Retryable("browser_content_timeout: 未等到语录卡片", document)

        # Apply robots Crawl-delay to requests as well as browser navigations.
        self.delay = max(self.delay, crawl_delay or 0)
        return retry_call(operation, self.active_log)

    def close(self):
        try:
            if self.browser:
                self.browser.quit()
        finally:
            self.session.close()


class DemoFetcher:
    def __init__(self, root, log, fault="none"):
        self.root, self.log, self.fault = root, log, fault
        self.calls = {}

    def get(self, source, page, url):
        def operation():
            key = source, page
            self.calls[key] = self.calls.get(key, 0) + 1
            if self.fault == "timeout" and source == "books" and page == 2 and self.calls[key] == 1:
                raise Retryable("SIMULATED timeout")
            filename = f"{source}_{page}.html"
            document = (self.root / "fixtures" / filename).read_text(encoding="utf-8")
            if source == "books" and page == 2:
                if self.fault == "selector":
                    document = document.replace("product_pod", "product_tile")
                elif self.fault == "dirty":
                    document = (self.root / "fixtures/books_dirty.html").read_text(encoding="utf-8")
            return document
        return retry_call(operation, lambda k, d: self.log(source, page, k, d), sleep=lambda seconds: None)

    def close(self):
        pass
