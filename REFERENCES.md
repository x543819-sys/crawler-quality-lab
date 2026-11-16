# GitHub参考与设计取舍

核对日期：2026-11-16。这里只借鉴设计思路，核心代码为本项目实现，没有直接复制这些仓库的实现文件。第三方库仍遵循各自许可证；本项目不重新分发它们的源码或二进制。

| 来源 | 核对内容 | 本项目采用 | 当前没有引入 |
|---|---|---|---|
| [Scrapy](https://github.com/scrapy/scrapy) | retry.py 与 LICENSE，BSD-3-Clause | 区分临时故障、限制重试次数、记录失败原因 | Scrapy完整调度器、分布式队列 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | README 与 Apache-2.0 LICENSE | 保留快照、区分采集与解析、使用内容版本比较 | 大型Web界面、第三方通知、代理服务、LLM总结 |
| [playwright-scraper-demo](https://github.com/joao-jleite/playwright-scraper-demo) | README、crawler.py、MIT LICENSE | 明确演示数据、有限重试、记录采集范围、无网测试、导出防公式误解析 | Playwright并发池、PDF及复杂Excel报表、模拟价格的商业结论 |

本项目改用 requests + lxml/XPath 处理静态页，DrissionPage + BeautifulSoup 处理动态页，直接对应岗位要求。数据库和页面检查点采用事务，增加MySQL适配、跨批次变化/未变记录区分、同批次重复隔离。暂不加入Selenium第二套浏览器实现，避免为堆技术名词增加维护负担。

查看过的文件版本标识（GitHub blob SHA，不代表整个仓库提交）：

- Scrapy retry.py：`314ed8ceaa839ebc2e47d25a9d8548c7be945a2f`
- Scrapy LICENSE：`4d0a0863ad6724fd6fdd10407169fe288a3cc95e`
- changedetection.io README：`9134a145b93a83d2f63f298ede53769fdc76a58a`
- changedetection.io LICENSE：`a6e09a45199d495dd9ff687399fb4835acb0b843`
- playwright-scraper-demo README：`de93633c317b78bbba57d9cedac99c8435d906b9`
- playwright-scraper-demo crawler.py：`87aa9df0ccf3322c9d7440d31334dc626a46614d`
- playwright-scraper-demo LICENSE：`97ca1e21557f3e194051e2e6e7d1e018d8452570`

搜索还找到若干图书+MySQL个人项目；本轮未完成其代码与许可证核验，没有据此复制实现或声称已验证其质量。

官方工具资料：

- DrissionPage： https://www.drissionpage.cn/browser_control/browser_options/
- requests： https://requests.readthedocs.io/en/latest/user/quickstart/
- SQLAlchemy： https://docs.sqlalchemy.org/en/20/core/connections.html
- MySQL下载与包校验： https://dev.mysql.com/downloads/mysql/8.4.html

网络抓取原始页面和渲染后页面存在区别。实测中发现当前DrissionPage 4.1.1.4的 auto_port 参数与旧4.0文档不同，已依据实际公开API签名调整，并通过真实续采验证；不依赖旧版示例未经运行就宣称可用。
