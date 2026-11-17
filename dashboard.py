"""Local read-only Streamlit dashboard. No scheduler or crawl starts on a rerun."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from crawler_lab.analytics import batch_details, history, reader_engine
from crawler_lab.report import spreadsheet_safe

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="采集与数据质量工作台", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>.block-container{padding-top:2rem;max-width:1450px}
[data-testid=stMetric]{background:#edf6f5;border-radius:12px;padding:16px}
h1{letter-spacing:-.03em}div[data-testid=stSidebar]{background:#f2f6fa}</style>""", unsafe_allow_html=True)
st.title("采集与数据质量工作台")
st.caption("两个公开练习站 · 批次追踪 · 数据质量 · 增量变化")
backend = st.sidebar.selectbox("数据库", ["sqlite", "mysql"], key="backend", format_func=lambda v: {"sqlite": "SQLite", "mysql": "MySQL"}[v])
mode = st.sidebar.radio("数据模式", ["demo", "live"], key="mode", format_func=lambda v: {"demo": "离线模拟", "live": "联网采集（练习站）"}[v])
auto_refresh = st.sidebar.checkbox("每15秒刷新", value=False)
st.sidebar.button("刷新数据")
st.sidebar.caption("看板只读。刷新不会新建采集任务。历史趋势最多展示最近200个批次。")
st.sidebar.info("先运行08启动离线定时采集；联网调度使用09。停止调度请在对应终端按Ctrl+C。")


def show_scheduler():
    path = ROOT / "data/scheduler/state.json"
    if not path.exists():
        st.info("尚未启动定时调度。看板与调度器可分别运行。")
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.info("暂时无法读取调度状态，请稍后刷新。")
        return
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(state.get("heartbeat_at", state["started_at"]))).total_seconds()
    status = state["status"] if state["status"] != "running" or age < 15 else "心跳已过期，服务可能停止"
    st.write(f"调度状态：{status} · 数据：{state['mode']} / {state['backend']} · 间隔：{state['interval_seconds']}秒")
    st.caption(f"下次触发：{state.get('next_run_at') or '—'}（含时区） · 本次服务已完成尝试：{state['completed_attempts']}")
    if state.get("last_result"):
        result = state["last_result"]
        st.write("最近调度结果：", result["status"], "批次：", result.get("run_id") or "未新建批次")


@st.fragment(run_every=15 if auto_refresh else None)
def render():
    show_scheduler()
    engine = None
    try:
        engine = reader_engine(ROOT, backend)
        frame = history(engine, mode)
        if frame.empty:
            st.info("当前数据库和模式还没有采集批次。请先运行对应采集入口。")
            return
        run_id = st.selectbox("选择采集批次（最新在前）", frame["run_id"].tolist(), key="batch")
        row = frame.loc[frame["run_id"].eq(run_id)].iloc[0]
        details = batch_details(engine, run_id, mode)
        st.write(f"当前批次：**{row['status']}** · {backend} / {mode}")
        cols = st.columns(4)
        cols[0].metric("完成／计划页", f"{row['completed_pages']} / {row['planned_pages']}")
        cols[1].metric("通过记录", str(row["accepted_rows"]))
        cols[2].metric("隔离记录", str(row["quarantined_rows"]))
        rate = row["quality_rate"]
        cols[3].metric("已解析记录通过率", "不可计算" if pd.isna(rate) else f"{rate:.1%}")
        st.caption("记录通过率不等于页面完成率；一个记录可有多个问题。价格和评分是练习站演示值。")
        trend, pages, quality, data = st.tabs(["批次趋势", "页面与故障", "隔离与质检", "数据与变化"])
        with trend:
            chart = frame.iloc[::-1].copy()
            chart["批次时间"] = pd.to_datetime(chart["started_at"], utc=True).dt.tz_convert("Asia/Shanghai")
            chart = chart.set_index("批次时间")
            st.subheader("每批次通过与隔离记录")
            st.line_chart(chart[["accepted_rows", "quarantined_rows"]].rename(columns={"accepted_rows": "通过记录", "quarantined_rows": "隔离记录"}))
            st.subheader("新增、内容变化与未变")
            st.bar_chart(chart[["new", "updated", "unchanged"]].rename(columns={"new": "新增", "updated": "内容变化", "unchanged": "内容未变"}), stack=True)
            st.dataframe(frame, hide_index=True, width="stretch")
        with pages:
            st.dataframe(pd.DataFrame(details["tasks"])[["source", "page", "status", "row_count", "error"]], hide_index=True, width="stretch")
            with st.expander("最近事件"):
                st.dataframe(pd.DataFrame(details["events"]), hide_index=True, width="stretch")
        with quality:
            if details["issues"]:
                st.bar_chart(pd.Series(details["issues"], name="问题次数"))
            else:
                st.info("当前展示范围内没有隔离问题；不代表已核实全部内容真实性。")
            st.dataframe(pd.DataFrame(details["quarantine"]), hide_index=True, width="stretch")
            if details["quarantine_truncated"]:
                st.caption("隔离明细及问题次数仅展示前1000条；完整隔离数量见上方指标。")
        with data:
            source = st.selectbox("来源", ["全部", "books", "quotes"])
            accepted = pd.DataFrame(details["accepted"])
            if source != "全部" and not accepted.empty:
                accepted = accepted.loc[accepted["source"].eq(source)]
            st.dataframe(accepted, hide_index=True, width="stretch")
            if details["observations_truncated"]:
                st.caption("明细限当前批次前1000条；汇总指标仍使用完整批次。")
            csv = accepted.map(spreadsheet_safe).to_csv(index=False).encode("utf-8-sig")
            st.download_button("下载当前明细 CSV", csv, file_name=f"{run_id}_{source}.csv", mime="text/csv")
            report = ROOT / "runs" / run_id / "report.html"
            if report.resolve().is_relative_to((ROOT / "runs").resolve()) and report.is_file():
                st.download_button("下载完整 HTML 报告", report.read_bytes(), file_name=f"{run_id}.html", mime="text/html")
    except Exception as error:
        st.error(f"数据暂不可用（{type(error).__name__}）。请确认所选数据库已有采集批次；MySQL需本机实例运行。")
    finally:
        if engine is not None:
            engine.dispose()


render()
