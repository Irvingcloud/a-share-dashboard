# -*- coding: utf-8 -*-
"""
定时抓取脚本 —— 供 Windows 计划任务调用。

流程：
  1. 判断今天是不是交易日，不是就记一行日志直接退出（不写记录、不关机）
  2. 抓数据 → 算指标 → 写入 历史记录/市场阶段历史.csv + .xlsx
  3. 写一行日志到 日志/定时抓取日志.txt
  4. （可选）执行关机，带倒计时，可取消

常用开关都在下面 CONFIG 区，改完保存即可，不用改别的地方。
"""

import os
import subprocess
import sys
from datetime import date, datetime

import pandas as pd

import market_core as mc

# ============================================================
# CONFIG
# ============================================================
SHUTDOWN = True                 # 抓完是否自动关机；不想关就改 False
SHUTDOWN_DELAY = 120            # 关机倒计时（秒），这段时间可以 shutdown /a 取消
SHUTDOWN_ON_FAILURE = False     # 抓取失败时是否也关机（默认 False：留着电脑方便你排查）

LOG_FILE = mc.LOG_DIR / "定时抓取日志.txt"
SKIP_FLAG = mc.HIST_DIR / "今天不关机.txt"

# 测试/临时取消用：设置环境变量 MSA_NO_SHUTDOWN=1 即可只抓取不关机
if os.environ.get("MSA_NO_SHUTDOWN") == "1":
    SHUTDOWN = False

# 云端（Linux / Streamlit Cloud / GitHub Actions）没有关机需求，自动关闭
if os.name != "nt":
    SHUTDOWN = False


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    line = f"[{mc.now_cn().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def detect_trading_day() -> bool:
    """优先用交易日历；日历接口挂了就退化为「周一至周五」"""
    try:
        return mc.is_trading_day(mc.today_cn(), mc.trade_dates())
    except Exception as e:
        log(f"交易日历不可用（{type(e).__name__}），退化为按周一~周五判断")
        return mc.today_cn().weekday() < 5


def do_shutdown(reason: str) -> None:
    if SKIP_FLAG.exists():
        log(f"检测到 {SKIP_FLAG.name}，跳过本次关机（不关机标记仍有效，可手动删除）")
        return
    log(f"{reason}，{SHUTDOWN_DELAY} 秒后关机（如需取消：命令行执行  shutdown /a）")
    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY), "/c",
             f"市场数据已保存，{SHUTDOWN_DELAY}秒后自动关机。取消请运行 shutdown /a"],
            check=False,
        )
    except Exception as e:
        log(f"关机命令执行失败：{e}")


def main() -> int:
    log("=" * 56)
    log("定时抓取开始")

    if not detect_trading_day():
        log("今天不是交易日，跳过抓取与关机")
        return 0

    errors, snap = [], None
    try:
        result = mc.collect()
        errors = result.get("errors", [])
        snap = result.get("snap")
        if result["detail"].empty:
            log("期指数据为空，本次记录可能不完整")
        else:
            r = result["detail"][result["detail"]["品种"] == "IF"].iloc[0]
            log(f"IF {r['近月合约']} 贴水 {r['近月贴水率(%)']}%，当季年化 {r['当季年化(%)']}%（{r['情绪']}）")
        if result["breadth"]:
            b = result["breadth"]
            trin_txt = "—" if b["trin"] is None else f"{b['trin']:.2f}"
            log(f"宽广度 {b['breadth']:.1f}%，TRIN {trin_txt}")
        if result["etf_sum"]:
            s = result["etf_sum"]
            log(f"ETF（{s['date']}）当日净申赎 {s['day_sum']:+.1f} 亿，近5日 {s['sum5']:+.1f} 亿")
        if result["overseas"]:
            o = result["overseas"]
            if "NQ" in o:
                log(f"纳指期货 {o['NQ']['price']:.0f}（{o['NQ']['chg']:+.2f}%）")
            if "IXIC" in o:
                log(f"纳斯达克指数（{o['IXIC']['date']}）{o['IXIC']['chg']:+.2f}%")
    except Exception as e:
        errors.append(f"采集异常: {type(e).__name__} {e}")
        log(f"采集异常：{type(e).__name__} {e}")

    for e in errors:
        log(f"  · {e}")

    if snap is None:
        log("没有生成有效快照（期指数据缺失），未写入历史记录")
        ok = False
    else:
        try:
            df = mc.save_snapshot(snap)
            if mc.HISTORY_BACKEND == "github" and mc._github_backend():
                where = "GitHub 仓库的 history.csv"
            elif mc.HISTORY_BACKEND == "feishu" and mc._feishu_backend():
                where = "飞书多维表格"
            else:
                where = f"{mc.HIST_CSV.name} 与 {mc.HIST_XLSX.name}"
            log(f"已写入 {where}，当前共 {len(df)} 条记录")
            ok = True
        except Exception as e:
            log(f"写入历史失败：{type(e).__name__} {e}（可能是 Excel 正开着该文件）")
            ok = False

    log("定时抓取结束")

    if SHUTDOWN and (ok or SHUTDOWN_ON_FAILURE):
        do_shutdown("数据处理完成" if ok else "抓取失败但已设置失败也关机")
    elif SHUTDOWN:
        log("抓取未成功，按设置保留电脑不关机")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
