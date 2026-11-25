# -*- coding: utf-8 -*-
"""
共享内核：抓数据、算指标、写历史记录。

网页（app.py）与定时脚本（daily_snapshot.py）都调用这里，
保证两边算出来的数字、字段顺序、口径完全一致。
"""

import calendar
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import akshare as ak
import pandas as pd

# 中国时区固定 +8（无夏令时），避免云端 UTC 服务器把日期算错一天
CN_TZ = timezone(timedelta(hours=8))


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def today_cn() -> date:
    return now_cn().date()


# ============================================================
# 路径
# ============================================================
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
HIST_DIR = DATA_DIR / "历史记录"
HIST_CSV = HIST_DIR / "市场阶段历史.csv"
HIST_XLSX = HIST_DIR / "市场阶段历史.xlsx"
LOG_DIR = DATA_DIR / "日志"

# 历史记录后端：local（CSV/Excel，本地用） | feishu（多维表格） | github（仓库 CSV，云端用）
def _inject_secrets() -> None:
    """Streamlit Cloud 的 Secrets 不在环境变量里，这里搬一次（本地/脚本环境自动跳过）"""
    keys = ("HISTORY_BACKEND", "APP_PASSWORD", "FEISHU_APP_ID",
            "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN", "FEISHU_TABLE_ID",
            "GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_HIST_PATH")
    try:
        import streamlit as st
        for k in keys:
            if os.environ.get(k):
                continue
            try:
                v = st.secrets[k]
            except Exception:
                continue
            if v:
                os.environ[k] = str(v)
    except Exception:
        pass


_inject_secrets()

HISTORY_BACKEND = os.environ.get("HISTORY_BACKEND", "local").strip().lower()

# 快照字段顺序（CSV / Excel / 飞书表格共用，顺序一致）
SNAPSHOT_COLUMNS = [
    "日期", "盘面简述",
    "IF近月合约", "IF近月贴水率(%)", "IF当季合约", "IF当季年化(%)", "IF期限形态", "IF中期情绪",
    "IH近月贴水率(%)", "IC近月贴水率(%)", "IM近月贴水率(%)",
    "市场宽广度(%)", "TRIN", "上涨额占比(%)", "两市成交额(亿)", "短线判断",
    "ETF份额日期", "ETF当日净申赎(亿)", "ETF近5日净申赎(亿)", "ETF合计成交额(亿)",
    "纳指期货点位", "纳指期货涨跌幅(%)", "纳斯达克指数涨跌幅(%)", "上证涨跌幅(%)",
]

# ============================================================
# 配置
# ============================================================
VARIETIES = {
    "IH": {"name": "上证50",   "index": "sh000016", "size": "大盘"},
    "IF": {"name": "沪深300",  "index": "sh000300", "size": "大盘"},
    "IC": {"name": "中证500",  "index": "sh000905", "size": "中小盘"},
    "IM": {"name": "中证1000", "index": "sh000852", "size": "中小盘"},
}

SINA_FUT_NAME = {
    "IH": "上证50指数期货",
    "IF": "沪深300指数期货",
    "IC": "中证500指数期货",
    "IM": "中证1000股指期货",
}

# 龙头宽基 ETF（均为上交所品种，份额数据可查）
ETF_LIST = [
    ("510050", "上证50ETF（华夏）"),
    ("510300", "沪深300ETF（华泰柏瑞）"),
    ("510310", "沪深300ETF（易方达）"),
    ("510330", "沪深300ETF（华夏）"),
    ("510500", "中证500ETF（南方）"),
    ("512500", "中证500ETF（华夏）"),
    ("512100", "中证1000ETF（南方）"),
    ("588000", "科创50ETF（华夏）"),
]

THRESHOLD = {
    "大盘":   {"极度悲观": -8.0, "悲观": -4.0, "中性上界": 1.0, "偏乐观": 3.0},
    "中小盘": {"极度悲观": -12.0, "悲观": -6.0, "中性上界": 1.0, "偏乐观": 3.0},
}

ETF_SIGNAL = {
    "强净申购": 60,   # 合计净申赎 > 60 亿
    "净申购": 15,
    "中性": -15,
}


# ============================================================
# 工具函数
# ============================================================
def third_friday(year: int, month: int) -> date:
    weeks = calendar.monthcalendar(year, month)
    fridays = [w[4] for w in weeks if w[4] != 0]
    return date(year, month, fridays[2] if len(fridays) >= 3 else fridays[-1])


def parse_expiry(code: str) -> date:
    digits = "".join(c for c in str(code) if c.isdigit())
    if len(digits) == 4:
        year, month = 2000 + int(digits[:2]), int(digits[2:])
        if 1 <= month <= 12:
            return third_friday(year, month)
    return third_friday(today_cn().year, today_cn().month)


def days_left(code: str) -> int:
    return (parse_expiry(code) - today_cn()).days


def annual_basis(future: float, spot: float, expiry: date) -> float:
    d = max((expiry - today_cn()).days, 1)
    return (future - spot) / spot * (365 / d) * 100


def mood_by_rate(rate: float, size: str) -> str:
    t = THRESHOLD[size]
    if rate <= t["极度悲观"]:
        return "极度悲观"
    if rate <= t["悲观"]:
        return "悲观"
    if rate <= t["中性上界"]:
        return "中性"
    if rate <= t["偏乐观"]:
        return "偏乐观"
    return "过热"


def mood_color(mood: str) -> str:
    """情绪→颜色。A股习惯：红=利好、绿=利空，情绪越悲观越绿"""
    return {"极度悲观": BEAR_STRONG, "悲观": BEAR, "中性": NEUTRAL,
            "偏乐观": BULL, "过热": BULL_STRONG}.get(mood, NEUTRAL)


# ============================================================
# 颜色口径（红=利好、绿=利空，深红/深绿=程度更重）
# ============================================================
BULL_STRONG = "C00000"   # 深红：大利好
BULL = "E53935"          # 红：利好
NEUTRAL = "444444"       # 中性
BEAR = "2E7D32"          # 绿：利空
BEAR_STRONG = "00701A"   # 深绿：大利空

LEVEL_HEX = {"强利好": BULL_STRONG, "利好": BULL, "中性": NEUTRAL,
             "利空": BEAR, "强利空": BEAR_STRONG}

# 方向 1：数值越大越利好（t1>t2>t3>t4）
# 方向 -1：数值越小越利好
_SCALE = {
    # —— 广度与量能
    "市场宽广度(%)": (1, 85, 70, 40, 25),
    "TRIN": (-1, 0.70, 1.00, 1.20, 1.50),
    "上涨额占比(%)": (1, 65, 55, 45, 35),
    # —— 期指基差（贴水越深越利空，升水才利好）
    "IF当季年化(%)": (1, 3.0, 1.0, -4.0, -8.0),
    "当季年化(%)": (1, 3.0, 1.0, -4.0, -8.0),
    "近月贴水率(%)": (1, 2.0, 0.5, -1.5, -3.0),
    "IF近月贴水率(%)": (1, 2.0, 0.5, -1.5, -3.0),
    "IH近月贴水率(%)": (1, 2.0, 0.5, -1.5, -3.0),
    "IC近月贴水率(%)": (1, 2.0, 0.5, -1.5, -3.0),
    "IM近月贴水率(%)": (1, 2.0, 0.5, -1.5, -3.0),
    "当季贴水率(%)": (1, 3.0, 1.0, -3.0, -5.0),
    "远月贴水率(%)": (1, 4.0, 1.0, -4.0, -6.0),
    # —— 资金
    "日净申赎(亿元)": (1, 60, 15, -15, -60),
    "近5日净申赎(亿元)": (1, 60, 15, -15, -60),
    "ETF当日净申赎(亿)": (1, 60, 15, -15, -60),
    "ETF近5日净申赎(亿)": (1, 60, 15, -15, -60),
    # —— 涨跌幅
    "涨跌幅(%)": (1, 1.5, 0.5, -0.5, -1.5),
    "上证涨跌幅(%)": (1, 1.5, 0.5, -0.5, -1.5),
    "纳指期货涨跌幅(%)": (1, 1.5, 0.5, -0.5, -1.5),
    "纳斯达克指数涨跌幅(%)": (1, 1.5, 0.5, -0.5, -1.5),
}

_NOT_COLORED = {"两市成交额(亿)", "IF中线位置", "日期", "盘面简述"}


def metric_level(metric: str, value) -> str | None:
    """返回 强利好 / 利好 / 中性 / 利空 / 强利空；无法判断返回 None"""
    if metric in ("IF期限形态", "期限形态"):
        s = str(value)
        if "修复" in s:
            return "利好"
        if "下行" in s:
            return "利空"
        return "中性"

    if "情绪" in metric:
        return {"极度悲观": "强利空", "悲观": "利空", "中性": "中性",
                "偏乐观": "利好", "过热": "强利好"}.get(str(value))

    if metric not in _SCALE:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    direction, t1, t2, t3, t4 = _SCALE[metric]
    if direction == 1:
        if v >= t1:
            return "强利好"
        if v >= t2:
            return "利好"
        if v >= t3:
            return "中性"
        if v >= t4:
            return "利空"
        return "强利空"
    else:
        if v <= t1:
            return "强利好"
        if v <= t2:
            return "利好"
        if v <= t3:
            return "中性"
        if v <= t4:
            return "利空"
        return "强利空"


def color_of(metric: str, value) -> str | None:
    lvl = metric_level(metric, value)
    return LEVEL_HEX.get(lvl) if lvl else None


def style_of(metric: str, value) -> str:
    """给 pandas Styler / 网页用：返回 CSS 片段"""
    hex_color = color_of(metric, value)
    return f"color:#{hex_color};font-weight:600" if hex_color else ""


def short_term_level(breadth: float, trin: float | None) -> tuple[str, str]:
    if trin is None:
        return "量能数据缺失", "仅有广度数据，无法判断量能是否配合"
    if breadth < 15 and trin < 0.5:
        return "超跌反弹窗口", "普跌但少数上涨股放量承接，短线存在技术性反弹条件"
    if breadth > 85 and trin > 1.5:
        return "超涨回调风险", "普涨但少数下跌股放量，资金在局部撤退"
    if breadth > 80 and trin > 1.2:
        return "普涨但量能背离", "家数普涨，但下跌股占据更多成交额，冲高回落风险上升"
    if breadth < 25 and trin < 0.8:
        return "缩量阴跌", "普跌且下跌股并未明显放量，无量下挫，杀伤力有限但难反弹"
    if trin < 0.7:
        return "量能偏多", "上涨股的成交额占比明显占优"
    if trin > 1.3:
        return "量能偏空", "下跌股的成交额占比明显占优"
    return "中性震荡", "涨跌家数与量能基本匹配，无明确方向"


# ============================================================
# 数据抓取（不带 Streamlit 缓存，网页端自行包一层）
# ============================================================
def load_spot_index() -> pd.DataFrame:
    return ak.index_zh_spot_sina()


def _sina_fut_quotes(codes: list[str]) -> dict[str, list[str]]:
    """直接读新浪期货行情（hq.sinajs.cn），返回 {合约代码: 字段列表}"""
    import requests
    url = "https://hq.sinajs.cn/list=" + ",".join(f"nf_{c}" for c in codes)
    r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn",
                                   "User-Agent": "Mozilla/5.0"}, timeout=12)
    out = {}
    for line in r.text.split(";"):
        if "hq_str_nf_" not in line:
            continue
        head, _, rest = line.partition("=")
        code = head.split("hq_str_nf_")[-1].strip()
        body = rest.strip().strip('"')
        if body:
            out[code] = body.split(",")
    return out


def _contract_codes(prod: str, months: int = 12) -> list[str]:
    """未来 N 个月的可交割合约代码（中金所每种只有当月/下月/当季/下季四个会返回数据）"""
    out, y, m = [], today_cn().year, today_cn().month
    for _ in range(months):
        out.append(f"{prod}{str(y)[2:]}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def load_futures() -> pd.DataFrame:
    """期指各合约实时行情。

    先用新浪 hq.sinajs.cn 逐合约取（akshare 的 futures_zh_realtime_sina 有时只返回
    已到期的老合约，贴水率会算成几十个点，不能直接用）。
    """
    rows = []
    today = today_cn()

    for prod in SINA_FUT_NAME:
        codes = _contract_codes(prod)
        try:
            quotes = _sina_fut_quotes(codes)
        except Exception:
            quotes = {}

        for code in codes:
            f = quotes.get(code)
            if not f or len(f) < 38:
                continue
            try:
                trade = float(f[3])
                position = float(f[6])
            except ValueError:
                continue
            if trade <= 0:
                continue
            if parse_expiry(code) < today:
                continue
            rows.append({
                "品种": prod, "symbol": code,
                "trade": trade, "position": position,
                "volume": float(f[4] or 0),
                "tradedate": f[36], "ticktime": f[37],
            })

    if rows:
        return pd.DataFrame(rows)

    # 兜底：新浪直连失败时退回 akshare
    fallback = []
    for code, name in SINA_FUT_NAME.items():
        try:
            df = ak.futures_zh_realtime_sina(symbol=name)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.copy()
        df = df[df["symbol"].astype(str).str.len() == 6]
        df = df[[parse_expiry(str(s)) >= today for s in df["symbol"]]]
        df["品种"] = code
        fallback.append(df)
    return pd.concat(fallback, ignore_index=True) if fallback else pd.DataFrame()


def load_breadth() -> dict:
    df = ak.stock_zh_a_spot_sina().copy()
    df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce").fillna(0)
    df = df[df["最新价"] > 0]

    up, down = df[df["涨跌幅"] > 0], df[df["涨跌幅"] < 0]
    up_amt, down_amt = up["成交额"].sum(), down["成交额"].sum()
    total = up_amt + down_amt
    up_n, down_n = len(up), len(down)

    breadth = up_n / (up_n + down_n) * 100 if (up_n + down_n) else 50.0
    trin = (up_n / down_n) / (up_amt / down_amt) if (down_n and down_amt) else None
    amt_share = up_amt / total * 100 if total else 50.0

    return {
        "up": up_n, "down": down_n,
        "breadth": breadth, "trin": trin,
        "amt_share": amt_share, "amt_spread": amt_share - breadth,
        "total_amt": total / 1e8, "up_amt": up_amt / 1e8, "down_amt": down_amt / 1e8,
        "limit_up": int((df["涨跌幅"] >= 9.8).sum()),
        "limit_down": int((df["涨跌幅"] <= -9.8).sum()),
        "source": "新浪全市场快照（口径最全）",
    }


def load_breadth_light() -> dict:
    df = ak.stock_market_activity_legu()
    d = {str(k): float(v) for k, v in zip(df.iloc[:, 0], df.iloc[:, 1])}
    up, down = d.get("上涨", 0), d.get("下跌", 0)
    breadth = up / (up + down) * 100 if (up + down) else 50.0
    return {
        "up": int(up), "down": int(down),
        "breadth": breadth, "trin": None,
        "amt_share": None, "amt_spread": None,
        "total_amt": None, "up_amt": None, "down_amt": None,
        "limit_up": int(d.get("涨停", 0)), "limit_down": int(d.get("跌停", 0)),
        "source": "乐咕乐股（仅家数，量能缺失）",
    }


def load_discount_history(code: str, window: int) -> pd.DataFrame:
    fut = ak.futures_main_sina(symbol=f"{code}0").copy()
    fut["d"] = pd.to_datetime(fut["日期"]).dt.date
    fut = fut[["d", "收盘价"]].rename(columns={"收盘价": "期货"})

    idx = ak.index_zh_daily_sina(symbol=VARIETIES[code]["index"]).copy()
    col = "date" if "date" in idx.columns else "日期"
    idx["d"] = pd.to_datetime(idx[col]).dt.date
    idx = idx[["d", "close"]].rename(columns={"close": "现货"})

    m = fut.merge(idx, on="d", how="inner").sort_values("d")
    m["贴水率(%)"] = (m["期货"] - m["现货"]) / m["现货"] * 100
    return m.tail(window).reset_index(drop=True)


def load_overseas() -> dict:
    """外盘：纳指期货 NQ、标普期货 ES、纳斯达克指数 .IXIC"""
    out = {}
    for sym, label in [("NQ", "纳指期货"), ("ES", "标普期货")]:
        try:
            df = ak.futures_foreign_hist_sina(symbol=sym).copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").dropna(subset=["close"])
            if len(df) < 2:
                continue
            last, prev = df.iloc[-1], df.iloc[-2]
            out[sym] = {
                "label": label,
                "price": float(last["close"]),
                "chg": (float(last["close"]) / float(prev["close"]) - 1) * 100,
                "date": last["date"].date(),
            }
        except Exception:
            continue

    try:
        ix = ak.index_us_daily_sina(symbol=".IXIC").copy()
        ix["date"] = pd.to_datetime(ix["date"])
        ix = ix.sort_values("date").dropna(subset=["close"])
        if len(ix) >= 2:
            last, prev = ix.iloc[-1], ix.iloc[-2]
            out["IXIC"] = {
                "label": "纳斯达克指数",
                "price": float(last["close"]),
                "chg": (float(last["close"]) / float(prev["close"]) - 1) * 100,
                "date": last["date"].date(),
            }
    except Exception:
        pass
    return out


def load_etf_quotes() -> pd.DataFrame:
    df = ak.fund_etf_category_sina(symbol="ETF基金").copy()
    df["代码"] = df["代码"].astype(str)
    for c in ["最新价", "涨跌幅", "成交额"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_etf_scale(n_days: int = 8) -> dict:
    """按交易日从新到旧抓取上交所 ETF 份额，返回 {date: DataFrame}"""
    cal = ak.calendar_trade_date_hist_sina()
    dates = [d.date() for d in pd.to_datetime(cal["trade_date"])]
    dates = [d for d in dates if d <= today_cn()][-n_days:]

    out = {}
    for d in reversed(dates):
        try:
            df = ak.fund_etf_scale_sse(date=d.strftime("%Y%m%d"))
        except Exception:
            continue
        if df is None or df.empty or "基金份额" not in df.columns:
            continue
        df = df.copy()
        df["基金代码"] = df["基金代码"].astype(str)
        df["基金份额"] = pd.to_numeric(df["基金份额"], errors="coerce")
        out[d] = df[df["基金代码"].isin([c for c, _ in ETF_LIST])]
    return out


# ============================================================
# 计算
# ============================================================
def extract_spot(spot_df: pd.DataFrame) -> tuple[dict, float | None, float | None]:
    """返回 (各品种现货点位, 上证点位, 上证涨跌幅)"""
    spot_price, sh_px, sh_chg = {}, None, None
    if spot_df is None or spot_df.empty:
        return spot_price, sh_px, sh_chg

    for code, info in VARIETIES.items():
        row = spot_df[spot_df["代码"] == info["index"]]
        if row.empty:
            row = spot_df[spot_df["代码"].astype(str).str.contains(info["index"].replace("sh", ""))]
        if not row.empty:
            spot_price[code] = float(row.iloc[0]["最新价"])

    sh = spot_df[spot_df["代码"] == "sh000001"]
    if not sh.empty:
        sh_px = float(sh.iloc[0]["最新价"])
        sh_chg = float(pd.to_numeric(sh.iloc[0]["涨跌幅"], errors="coerce"))
    return spot_price, sh_px, sh_chg


def build_detail(fut_raw: pd.DataFrame, spot_price: dict) -> pd.DataFrame:
    """期指基差明细表：近月 / 当季 / 远月"""
    if fut_raw is None or fut_raw.empty or not spot_price:
        return pd.DataFrame()

    fut_raw = fut_raw.copy()
    for c in ["trade", "position", "volume"]:
        fut_raw[c] = pd.to_numeric(fut_raw[c], errors="coerce")

    rows = []
    for code, grp in fut_raw.groupby("品种"):
        grp = grp.sort_values("position", ascending=False)
        spot = spot_price.get(code)
        if spot is None or grp.empty:
            continue

        def disc(price: float) -> float:
            return (price - spot) / spot * 100

        main = grp.iloc[0]
        m_code, m_price = str(main["symbol"]), float(main["trade"])

        cand = grp[[50 <= days_left(str(s)) <= 150 for s in grp["symbol"]]]
        if not cand.empty:
            q = cand.sort_values("position", ascending=False).iloc[0]
            q_code, q_price = str(q["symbol"]), float(q["trade"])
        else:
            q_code, q_price = m_code, m_price

        far_cand = grp[grp["position"] > 0]
        f = max(far_cand.itertuples(), key=lambda r: days_left(str(r.symbol))) if not far_cand.empty else main
        f_code, f_price = str(getattr(f, "symbol", m_code)), float(getattr(f, "trade", m_price))

        near_d, q_d, far_d = disc(m_price), disc(q_price), disc(f_price)
        shape = ("远月折价更深（曲线下行）" if far_d < q_d < near_d
                 else "远月折价更浅（曲线修复）" if far_d > near_d else "曲线相对平坦")

        rows.append({
            "品种": code, "指数": VARIETIES[code]["name"], "风格": VARIETIES[code]["size"],
            "近月合约": m_code, "近月基差点数": round(m_price - spot, 2),
            "近月贴水率(%)": round(near_d, 2),
            "当季合约": q_code, "当季贴水率(%)": round(q_d, 2),
            "当季年化(%)": round(annual_basis(q_price, spot, parse_expiry(q_code)), 2),
            "远月合约": f_code, "远月贴水率(%)": round(far_d, 2),
            "期限形态": shape, "持仓量": int(main["position"]),
            "数据日期": str(main.get("tradedate", "")),
            "情绪": mood_by_rate(annual_basis(q_price, spot, parse_expiry(q_code)), VARIETIES[code]["size"]),
        })
    return pd.DataFrame(rows)


def build_etf(quotes: pd.DataFrame, scale: dict) -> tuple[pd.DataFrame, dict | None, str]:
    """ETF 份额表 + 汇总。返回 (表格, 汇总, 提示语)"""
    if not scale:
        return pd.DataFrame(), None, ""

    scale_dates = sorted(scale.keys(), reverse=True)
    d0 = scale_dates[0]
    d_prev = scale_dates[1] if len(scale_dates) > 1 else None
    d_5 = scale_dates[min(4, len(scale_dates) - 1)]

    def shares_map(d):
        if d is None or d not in scale:
            return {}
        return dict(zip(scale[d]["基金代码"], scale[d]["基金份额"]))

    s0, s_prev, s_5 = shares_map(d0), shares_map(d_prev), shares_map(d_5)

    quote_map = {}
    if quotes is not None and not quotes.empty:
        quote_map = {r["代码"]: r for _, r in quotes.iterrows()}

    rows = []
    for code, label in ETF_LIST:
        q = quote_map.get(f"sh{code}", quote_map.get(f"sz{code}"))
        price = float(q["最新价"]) if q is not None else float("nan")
        chg = float(q["涨跌幅"]) if q is not None else float("nan")
        amt = float(q["成交额"]) / 1e8 if q is not None else float("nan")

        sh0 = s0.get(code)
        sh_prev = s_prev.get(code)
        sh_5 = s_5.get(code)
        if sh0 is None:
            continue

        delta = (sh0 - sh_prev) if (sh_prev is not None and pd.notna(sh_prev)) else float("nan")
        delta_amt = delta * price / 1e8 if pd.notna(delta) else float("nan")
        delta5_amt = (sh0 - sh_5) * price / 1e8 if (sh_5 is not None and pd.notna(sh_5)) else float("nan")

        rows.append({
            "代码": code, "名称": label,
            "最新价": round(price, 3) if pd.notna(price) else None,
            "涨跌幅(%)": round(chg, 2) if pd.notna(chg) else None,
            "成交额(亿)": round(amt, 2) if pd.notna(amt) else None,
            "份额(亿份)": round(sh0 / 1e8, 2),
            "日增份额(亿份)": round(delta / 1e8, 2) if pd.notna(delta) else None,
            "日净申赎(亿元)": round(delta_amt, 2) if pd.notna(delta_amt) else None,
            "近5日净申赎(亿元)": round(delta5_amt, 2) if pd.notna(delta5_amt) else None,
        })

    table = pd.DataFrame(rows)
    if table.empty:
        return table, None, ""

    day_sum = table["日净申赎(亿元)"].sum(skipna=True)
    sum5 = table["近5日净申赎(亿元)"].sum(skipna=True)
    amt_sum = table["成交额(亿)"].sum(skipna=True)
    summary = {
        "date": d0, "prev": d_prev, "day_sum": day_sum, "sum5": sum5, "amt_sum": amt_sum,
        "count": len(table),
    }
    return table, summary, ""


def etf_signal_text(day_sum: float) -> str:
    if day_sum > ETF_SIGNAL["强净申购"]:
        return "强净申购（大资金进场痕迹）"
    if day_sum > ETF_SIGNAL["净申购"]:
        return "温和净申购"
    if day_sum < ETF_SIGNAL["中性"]:
        return "净赎回（资金流出）"
    return "申赎基本平衡"


def etf_signal_short(day_sum: float) -> str:
    if day_sum > ETF_SIGNAL["强净申购"]:
        return f"ETF强净申购{day_sum:+.0f}亿"
    if day_sum > ETF_SIGNAL["净申购"]:
        return f"ETF温和净申购{day_sum:+.0f}亿"
    if day_sum < ETF_SIGNAL["中性"]:
        return f"ETF净赎回{day_sum:+.0f}亿"
    return f"ETF申赎平衡{day_sum:+.0f}亿"


def market_verdict(detail: pd.DataFrame, breadth: dict | None, etf_sum: dict | None,
                   overseas: dict | None = None) -> str:
    """当日盘面一句话解释：期指情绪｜ETF 资金｜短线量能｜外盘"""
    parts = []
    if detail is not None and not detail.empty:
        r = detail[detail["品种"] == "IF"].iloc[0]
        parts.append(f"期指{r['情绪']}（当季年化 {r['当季年化(%)']}%）")
    if etf_sum is not None:
        parts.append(etf_signal_short(etf_sum["day_sum"]))
    if breadth:
        parts.append(short_term_level(breadth["breadth"], breadth["trin"])[0])
    if overseas:
        nq, ix = overseas.get("NQ"), overseas.get("IXIC")
        if nq and ix:
            if nq["chg"] > 1 and ix["chg"] < -1:
                parts.append("外盘恐慌修复")
            elif nq["chg"] < -1:
                parts.append("外盘承压")
            elif nq["chg"] > 1:
                parts.append("外盘转强")
            else:
                parts.append("外盘中性")
        elif nq:
            parts.append(f"纳指期货{nq['chg']:+.1f}%")
    return "；".join(parts)


# ============================================================
# 历史记录
# ============================================================
def _feishu_backend():
    """云端模式且凭证齐全时返回 feishu 模块，否则 None"""
    if HISTORY_BACKEND != "feishu":
        return None
    try:
        import storage_feishu as sf
        if sf.configured():
            return sf
    except Exception:
        pass
    return None


def _github_backend():
    """云端模式且凭证齐全时返回 github 存储模块，否则 None"""
    if HISTORY_BACKEND != "github":
        return None
    try:
        import storage_github as sg
        if sg.configured():
            return sg
    except Exception:
        pass
    return None


def load_history() -> pd.DataFrame:
    gh = _github_backend()
    if gh is not None:
        try:
            df = gh.read_dataframe(SNAPSHOT_COLUMNS)
            df = df.drop(columns=["记录时间"], errors="ignore")
            return df.sort_values("日期", ignore_index=True) if not df.empty else df
        except Exception:
            pass  # 网络/权限问题则退回本地，页面不至于空白

    sf = _feishu_backend()
    if sf is not None:
        try:
            df = sf.read_dataframe(SNAPSHOT_COLUMNS)
            df = df.drop(columns=["记录时间"], errors="ignore")
            return df.sort_values("日期", ignore_index=True) if not df.empty else df
        except Exception:
            pass  # 网络/权限问题则退回本地，页面不至于空白

    if HIST_CSV.exists():
        try:
            df = pd.read_csv(HIST_CSV, encoding="utf-8-sig")
            df = df.drop(columns=["记录时间"], errors="ignore")
            return df.sort_values("日期", ignore_index=True) if not df.empty else df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _paint_excel(path: Path) -> None:
    """给 Excel 上色：红/绿字体区分利好利空，表头加粗+冻结"""
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except Exception:
        return

    try:
        wb = load_workbook(path)
    except Exception:
        return

    ws = wb.active
    ws.freeze_panes = "B2"
    headers = [c.value for c in ws[1]]

    for idx, name in enumerate(headers, start=1):
        head = ws.cell(row=1, column=idx)
        head.font = Font(bold=True, color="FFFFFF")
        head.fill = PatternFill("solid", fgColor="305496")
        head.alignment = Alignment(horizontal="center", vertical="center")

        width = 11
        if name == "盘面简述":
            width = 62
        elif name in ("日期", "ETF份额日期"):
            width = 12
        elif name and ("合约" in str(name) or "形态" in str(name) or "情绪" in str(name)):
            width = 16
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

        for r in range(2, ws.max_row + 1):
            cell = ws.cell(row=r, column=idx)
            lvl = metric_level(str(name), cell.value)
            if lvl:
                cell.font = Font(bold=lvl != "中性", color=LEVEL_HEX[lvl])
            if name == "盘面简述":
                cell.alignment = Alignment(wrap_text=True, vertical="center")

    try:
        wb.save(path)
    except Exception:
        pass


def save_snapshot(row: dict) -> pd.DataFrame:
    """写一条当日记录：云端写 GitHub 仓库 CSV（或飞书多维表格），本地写 CSV/Excel"""
    row = {k: row.get(k) for k in SNAPSHOT_COLUMNS if k in row} | \
          {k: v for k, v in row.items() if k not in SNAPSHOT_COLUMNS}

    # 只接受"今天"的快照：期指取数异常时日期会漂成老合约的到期日（如 2024-09-20），
    # 那种脏数据一律不落库
    d = str(row.get("日期") or "")[:10]
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        dt = None
    if dt is not None and abs((today_cn() - dt).days) > 7:
        print(f"[跳过写入] 快照日期 {d} 不是今天，疑似合约数据异常")
        return load_history()

    gh = _github_backend()
    if gh is not None:
        try:
            gh.upsert(row, key_field="日期", columns=SNAPSHOT_COLUMNS)
            return load_history()
        except Exception as e:
            print(f"[GitHub 写入失败] {type(e).__name__}: {e}（已退回本地文件）")

    sf = _feishu_backend()
    if sf is not None:
        try:
            sf.upsert(row, key_field="日期", columns=SNAPSHOT_COLUMNS)
            return load_history()
        except Exception as e:
            print(f"[飞书写入失败] {type(e).__name__}: {e}（已退回本地文件）")

    HIST_DIR.mkdir(parents=True, exist_ok=True)
    df = load_history()
    if not df.empty and "日期" in df.columns:
        df = df[df["日期"].astype(str) != str(row["日期"])]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.reindex(columns=[c for c in SNAPSHOT_COLUMNS if c in df.columns] +
                            [c for c in df.columns if c not in SNAPSHOT_COLUMNS])
    df = df.sort_values("日期")
    df.to_csv(HIST_CSV, index=False, encoding="utf-8-sig")
    try:
        df.to_excel(HIST_XLSX, index=False, sheet_name="日度记录")
        _paint_excel(HIST_XLSX)
    except Exception:
        pass
    return df


def build_snapshot(detail: pd.DataFrame, breadth: dict | None, etf_sum: dict | None,
                   overseas: dict, sh_chg: float | None) -> dict | None:
    """组装当日一条历史记录（字段顺序即 CSV 列顺序）"""
    if detail is None or detail.empty:
        return None

    if_row = detail[detail["品种"] == "IF"].iloc[0]
    snap = {
        "日期": str(if_row["数据日期"] or today_cn()),
        "盘面简述": market_verdict(detail, breadth, etf_sum, overseas),
        "IF近月合约": if_row["近月合约"],
        "IF近月贴水率(%)": if_row["近月贴水率(%)"],
        "IF当季合约": if_row["当季合约"],
        "IF当季年化(%)": if_row["当季年化(%)"],
        "IF期限形态": if_row["期限形态"],
        "IF中期情绪": if_row["情绪"],
    }
    for code in ["IH", "IC", "IM"]:
        r = detail[detail["品种"] == code]
        snap[f"{code}近月贴水率(%)"] = float(r.iloc[0]["近月贴水率(%)"]) if not r.empty else None

    if breadth:
        snap.update({
            "市场宽广度(%)": round(breadth["breadth"], 2),
            "TRIN": None if breadth["trin"] is None else round(breadth["trin"], 2),
            "上涨额占比(%)": None if breadth["amt_share"] is None else round(breadth["amt_share"], 2),
            "两市成交额(亿)": None if breadth["total_amt"] is None else round(breadth["total_amt"], 1),
            "短线判断": short_term_level(breadth["breadth"], breadth["trin"])[0],
        })
    if etf_sum is not None:
        snap.update({
            "ETF份额日期": str(etf_sum["date"]),
            "ETF当日净申赎(亿)": round(etf_sum["day_sum"], 1),
            "ETF近5日净申赎(亿)": round(etf_sum["sum5"], 1),
            "ETF合计成交额(亿)": round(etf_sum["amt_sum"], 1),
        })
    if overseas and "NQ" in overseas:
        snap["纳指期货点位"] = round(overseas["NQ"]["price"], 1)
        snap["纳指期货涨跌幅(%)"] = round(overseas["NQ"]["chg"], 2)
    if overseas and "IXIC" in overseas:
        snap["纳斯达克指数涨跌幅(%)"] = round(overseas["IXIC"]["chg"], 2)
    if sh_chg is not None:
        snap["上证涨跌幅(%)"] = round(sh_chg, 2)
    return snap


# ============================================================
# 一键采集（定时脚本用）
# ============================================================
def collect() -> dict:
    """抓全部数据并算好，任何一块失败都不影响其余部分"""
    out = {"detail": pd.DataFrame(), "breadth": None, "etf_table": pd.DataFrame(),
           "etf_sum": None, "overseas": {}, "snap": None, "errors": []}

    try:
        spot_df = load_spot_index()
        spot_price, sh_px, sh_chg = extract_spot(spot_df)
        out["sh_px"], out["sh_chg"] = sh_px, sh_chg
        out["detail"] = build_detail(load_futures(), spot_price)
        if out["detail"].empty:
            out["errors"].append("期指数据为空")
    except Exception as e:
        out["sh_px"], out["sh_chg"] = None, None
        out["errors"].append(f"期指/现货异常: {type(e).__name__} {e}")

    try:
        out["breadth"] = load_breadth()
    except Exception as e:
        try:
            out["breadth"] = load_breadth_light()
            out["errors"].append(f"全市场快照失败已降级: {type(e).__name__}")
        except Exception as e2:
            out["errors"].append(f"广度失败: {type(e2).__name__} {e2}")

    if out["breadth"] and not breadth_ok(out["breadth"]):
        out["errors"].append(
            f"涨跌家数仅 {out['breadth']['up']}/{out['breadth']['down']}，"
            "属未开盘或数据未更新，本次跳过广度指标"
        )
        out["breadth"] = None

    try:
        quotes, scale = load_etf_quotes(), load_etf_scale(8)
        out["etf_table"], out["etf_sum"], _ = build_etf(quotes, scale)
    except Exception as e:
        out["errors"].append(f"ETF 失败: {type(e).__name__} {e}")

    try:
        out["overseas"] = load_overseas()
    except Exception as e:
        out["errors"].append(f"外盘失败: {type(e).__name__} {e}")

    out["snap"] = build_snapshot(out["detail"], out["breadth"], out["etf_sum"],
                                out["overseas"], out.get("sh_chg"))
    return out


# ============================================================
# 交易日判断
# ============================================================
def trade_dates() -> list[date]:
    cal = ak.calendar_trade_date_hist_sina()
    return [d.date() for d in pd.to_datetime(cal["trade_date"])]


def breadth_ok(b: dict | None) -> bool:
    """涨跌家数太少基本是「未开盘 / 数据没更新」，这种广度不能用"""
    return bool(b) and (b.get("up", 0) + b.get("down", 0)) >= 100


def is_trading_day(d: date | None = None, cache: list[date] | None = None) -> bool:
    d = d or today_cn()
    days = cache if cache is not None else trade_dates()
    return d in days
