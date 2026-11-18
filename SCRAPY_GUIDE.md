# Scrapy进阶：框架、代理与验证码处理

## 先体验，再学原理

| 入口 | 内容 | 预期结果 |
|---|---|---|
| 10_Scrapy离线采集.cmd | Scrapy读取本地2页图书夹具 | 2/2页、3条通过 |
| 11_模拟代理故障切换.cmd | 受控下载器模拟第一个代理连接失败 | 失败代理冷却、切换另一个；1次重试，最后3条通过 |
| 12_模拟验证码拦截.cmd | 第2页返回模拟验证码页面 | 1页完成、1页失败、2条通过，partial；不是整个任务成功 |
| 13_Scrapy图书联网采集.cmd | 公开练习站图书前2页 | 网络正常时40条图书；不是全站采集 |

当前Scrapy入口只迁移静态图书采集。动态语录仍使用原有DrissionPage入口；未实现Scrapy动态浏览器整合。Scrapy离线仅3条图书，不同于原版图书+语录共6条，需按计划范围解释数量。

## 数据经过哪些模块

```mermaid
flowchart LR
  S[Spider 安排页面请求] --> M[Downloader Middleware 代理与拦截检测]
  M --> P[PageItem 页面数据]
  P --> I[Pipeline 解析字段与质检]
  I --> D[原有事务入库和页面检查点]
  D --> R[HTML报告和Streamlit看板]
```

Spider决定采集哪些页面；下载中间件处理网络、代理和响应；Pipeline把页面解析成图书记录并调用原有质检和入库逻辑。本项目把每页封装成PageItem，保持整页记录和检查点同事务提交。SQLite/MySQL、隔离记录和内容版本均复用原模块；工作进程仍受原worker.lock约束。数据库操作为同步、小页数验证实现，不宣称高并发数据库性能。

## 代理池实现边界

ProxyPool支持HTTP代理轮询、连接失败冷却60秒、跳过不可用代理；全部冷却时明确失败，不静默直连。重试请求清除旧代理元数据，避免继续使用故障代理。事件只记录proxy_1等编号，不记录认证信息。请求重试上限2次（含首次最多3次）。403、验证码和过长Retry-After不通过反复换代理处理。

11使用模拟传输，不会连接其中的127.0.0.1示例代理，验证的是池状态和Scrapy中间件流程。没有购买/测试外部代理服务，不声称已验证真实代理可用率、匿名性或IP地区。本版只接受不含用户名密码的HTTP代理URL；不支持SOCKS或带认证代理。

确有自己可用的HTTP代理时，复制proxy_config.example.json为proxy_config.json，在proxies数组填入实际代理地址。该本机文件已被Git忽略，请勿上传。默认无代理，不依赖代理池也可运行练习站采集；本入口不自动读取系统代理环境变量。

```powershell
python run_scrapy.py --mode live --book-pages 2 --proxy-config proxy_config.json
```

## 验证码与限流

通过验证码标题、常见表单/组件结构，以及401/403状态识别部分拦截场景，保留页面快照、manual_review_required事件和失败页，暂停当前批次。只在正文普通出现captcha一词不会直接判定为验证码；此检测是有限规则，不覆盖所有验证码或反爬形式。

没有接入自动识别、打码平台或真实站点验证码解决流程。人工需要先核实站点访问要求；当前未实现登录浏览器状态导入，原样续采仍可能再次被拦截。下面的演示只是移除模拟验证码、验证断点恢复，不证明能解决真实验证码：

```powershell
# 先运行12，复制生成的批次编号：
python run_scrapy.py --resume 此处替换为批次编号 --demo-scenario none
```

429等可重试响应尊重Retry-After；最多等待30秒，超过预算时停止本轮并记录，不能缩短服务端要求的等待时间。普通HTTP重试采用有限指数等待。联网下载使用串行请求、1秒基础间隔、AutoThrottle和robots规则，没有并发压力测试或规避站点限制的验收。

## 实测与学习顺序

2026-11-18：Scrapy 2.20.0在Windows独立环境实际运行；离线2页3条，模拟代理连接失败后1次重试完成，验证码暂停后只续采失败页，Retry-After=120时停止；公开练习站1页20条联网验证。证据见evidence/scrapy_verification.json，单元/回归结果见evidence/pytest.log。

先学习Spider、Request/Response，再看Downloader Middleware、Item/Pipeline，最后读代理冷却、重试和人工复核状态。代码由AI辅助开发，运行成功与独立掌握需分别描述。

API依据：[Scrapy下载中间件](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html)、[Windows异步运行说明](https://docs.scrapy.org/en/latest/topics/asyncio.html)。依赖版本记录在requirements-lock.txt，换电脑需重新运行setup.cmd安装Scrapy。
