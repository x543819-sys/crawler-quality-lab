# 定时调度与数据质量看板

## 先打开看板

当前电脑双击07，访问 http://127.0.0.1:8501 。首次打开选择SQLite、离线模拟；想看之前的60条联网结果，选择MySQL、联网采集。后者要求本机MySQL实例运行。看板不会自动启动数据库或创建表，也不会静默回退到SQLite。

看板只读；打开、刷新、切换批次不会启动采集。包含每批次通过/隔离趋势，新增/变化/未变统计，页面状态及错误、隔离原因、数据明细、CSV与原有HTML报告下载。模拟与联网、两种数据库分别展示。时间趋势按Asia/Shanghai显示。

历史最多展示最近200个批次；单批次明细最多1000条，达到上限时提示。汇总数量仍使用完整批次，隔离问题次数在截断时仅按展示的明细计算。记录通过率不替代页面完成率；未发现字段问题不等于已核实全部内容真实性。

## 再启动自动采集

08立即运行一次离线采集，然后每30分钟运行一次。09则使用MySQL联网采集，每个来源2页。联网入口会复用当前电脑已有的测试MySQL启动配置。

看板和调度器是两个独立进程；在调度窗口按Ctrl+C停止。关闭网页不会停止调度。重复启动调度器会被进程锁拦截；同一采集任务最多运行一份，采集进程忙碌时跳过该次调度并记录，不算成成功采集。失败尝试保留退出码、批次号和日志，下个周期新建批次；已有失败页面仍可使用原resume入口续采。

APScheduler采用3.x接口，max_instances=1、coalesce=True、misfire_grace_time=60。联网调度间隔至少15分钟，默认30分钟，仍沿用原有请求限速和重试规则。状态、心跳和调度尝试保存在本机data/scheduler目录，不上传连接配置或密码。

电脑必须开机且调度进程运行；休眠或关机期间不采集。当前使用内存调度，重启后按新启动时间重新计时，不补跑关机期间的任务。未安装Windows系统服务，也未完成长期生产值守或多机调度验证。调度心跳过期时看板提示服务可能停止，不将旧状态当作服务仍在运行的证明。

## 命令行

在项目目录使用项目Python环境：

```powershell
python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
python schedule_crawler.py --mode demo --backend sqlite --interval-seconds 1800 --run-now
python schedule_crawler.py --mode live --backend mysql --interval-seconds 1800 --run-now
# 完成两次后退出的离线验收：
python schedule_crawler.py --mode demo --interval-seconds 5 --run-now --max-runs 2
```

换电脑先运行setup.cmd安装新增依赖，再配置MySQL。支持的实测依赖版本记录在requirements-lock.txt。

## 验证与参考

2026-11-17实测：42项自动化测试通过；APScheduler 3.11.3、Streamlit 1.67.0；两轮离线定时采集成功，重叠触发被跳过；浏览器页面正常显示；MySQL只读查询核对4页、60条既有联网记录。摘要见evidence/product_verification.json，截图见evidence/dashboard_preview.png。调度验收结束后状态为stopped；没有留下持续联网采集任务。

测试覆盖只读连接、模拟/联网隔离、失败页无记录通过率、明细截断、忙碌跳过、失败记录、参数校验和看板切换/刷新不触发采集。AppTest需要允许本机socket通信的环境；测试没有通过跳过来掩盖沙箱限制。

API参考：[APScheduler 3.x用户指南](https://apscheduler.readthedocs.io/en/3.x/userguide.html)、[Streamlit fragment](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)、[Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)。
