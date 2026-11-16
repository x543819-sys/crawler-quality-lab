"""HTML parsing stays independent of network and database code."""
import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from lxml import html


class ParseError(ValueError):
    pass


def tidy(value):
    return " ".join(str(value or "").split())


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_books(document, page_url, *, card_class="product_pod"):
    tree = html.fromstring(document)
    cards = tree.xpath('//article[contains(concat(" ",normalize-space(@class)," "), $token)]',
                       token=f" {card_class} ")
    if not cards:
        raise ParseError("book_selector_empty: 当前选择器未找到商品卡片，不能当作成功采集0条")
    rows = []
    for card in cards:
        links = card.xpath('.//h3/a')
        link = links[0] if links else None
        url = urldefrag(urljoin(page_url, link.get("href", "")))[0] if link is not None else ""
        title = tidy(link.get("title") or link.text_content()) if link is not None else ""
        prices = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," price_color ")]/text()')
        price_raw = tidy(prices[0]) if prices else ""
        price_match = re.fullmatch(r"([£$€])\s*(\d+(?:\.\d{1,2})?)", price_raw)
        price = str(Decimal(price_match[2]).quantize(Decimal(".01"))) if price_match else ""
        currency = {"£": "GBP", "$": "USD", "€": "EUR"}.get(price_match[1], "") if price_match else ""
        stars = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," star-rating ")]/@class')
        rating = next((i for i, name in enumerate(["One", "Two", "Three", "Four", "Five"], 1)
                       if stars and name in stars[0].split()), 0)
        stocks = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," availability ")]')
        rows.append({"external_id": url, "title": title, "price": price, "price_raw": price_raw,
                     "currency": currency, "rating": rating,
                     "availability": tidy(stocks[0].text_content()) if stocks else "", "url": url})
    return rows


def parse_quotes(document, page_url):
    soup = BeautifulSoup(document, "html.parser")
    cards = soup.select("div.quote")
    if not cards:
        raise ParseError("quote_selector_empty: 动态内容未就绪或页面结构变化")
    rows = []
    for card in cards:
        text, author = card.select_one(".text"), card.select_one(".author")
        content = tidy(text.get_text()) if text else ""
        person = tidy(author.get_text()) if author else ""
        rows.append({"external_id": digest(content + "\n" + person), "text": content,
                     "author": person, "tags": [tidy(t.get_text()) for t in card.select(".tags .tag")],
                     "url": "https://quotes.toscrape.com/js/"})
    return rows


def validate(source, row):
    issues = []
    required = ["external_id", "title", "price", "currency", "availability", "url"] if source == "books" else ["external_id", "text", "author", "url"]
    for field in required:
        if not tidy(row.get(field)):
            issues.append("missing_" + field)
    if source == "books":
        try:
            price = Decimal(str(row.get("price", "")))
            if not price.is_finite() or price <= 0:
                issues.append("invalid_price")
        except InvalidOperation:
            issues.append("invalid_price")
        if row.get("currency") not in {"GBP", "USD", "EUR"}:
            issues.append("invalid_currency")
        if row.get("rating") not in range(1, 6):
            issues.append("invalid_rating")
    expected_host = "books.toscrape.com" if source == "books" else "quotes.toscrape.com"
    if urlparse(row.get("url", "")).hostname != expected_host:
        issues.append("unexpected_url_host")
    return list(dict.fromkeys(issues))


PARSERS = {"books": parse_books, "quotes": parse_quotes}
