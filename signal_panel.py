# -*- coding: utf-8 -*-
"""信号体检：把三年回测里最稳的几条条件，做成每天可自动判定的「分层」。

口径说明（重要）：
- 3 年报告里用「近月年化贴水」，临近交割时会被机械放大、换月会跳，
  依赖它的「深贴水」组合（72.7%）在换成稳定的「当季年化」后只剩 50%——
  说明那部分是交割周假象。本面板一律改用当季年化（dte 50–150），
  阈值与胜率都在 signal_ref.csv 上重算过。
- 阈值 = 近三年（743 个交易日）全样本的 30% / 70% 分位。

参考表由 build_signal_ref.py 生成，随数据更新重跑即可。
"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent
REF_FILE = BASE_DIR / "signal_ref.csv"

# 规则：命中 → 历史表现（全部来自 signal_ref.csv 重算，n≥40，前后两段分开验证）
RULES = [
    {
        "key": "strong",
        "name": "增强：贴水 5 日修复 + 广度强",
        "conds": [("d_ann5", "high"), ("hl20", "high")],
        "win20": 69.2, "mean20": 1.27, "win60": 65.1, "mean60": 4.55,
        "n": 66, "seg": "前段 70.4% / 后段 68.4%",
        "read": "当季年化贴水 5 日内明显收窄（期指在修复），同时全市场 20 日新高家数占优",
    },
    {
        "key": "main",
        "name": "主信号：放量 + 动量为正",
        "conds": [("vol_ratio", "high"), ("mom20", "high")],
        "win20": 66.4, "mean20": 1.26, "win60": 44.9, "mean60": 1.53,
        "n": 107, "seg": "前段 67.3% / 后段 65.5%",
        "read": "两市成交量高于 20 日均量、且沪深300 20 日动量为正——样本最大、最不衰减",
    },
    {
        "key": "bonus",
        "name": "加分：广度强 + 放量 + 动量为正",
        "conds": [("hl20", "high"), ("vol_ratio", "high"), ("mom20", "high")],
        "win20": 62.5, "mean20": 1.22, "win60": 52.1, "mean60": 3.45,
        "n": 48, "seg": "前段 61.9% / 后段 63.0%",
        "read": "三重同时成立时 60 日表现更稳；单独看胜率不如前两条，作用是把持续性拉长",
    },
    {
        "key": "avoid",
        "name": "回避：贴水修复 + 跌破 20 日均线",
        "conds": [("d_ann5", "high"), ("dev20", "low")],
        "win20": 36.2, "mean20": 0.25, "win60": 55.8, "mean60": 2.00,
        "n": 50, "seg": "前段 37.0% / 后段 35.0%",
        "read": "期指贴水在收窄（情绪回暖）但指数还在 20 日均线下方——价格与情绪背离，反弹容易失败",
    },
]

FEATURE_LABELS = {
    "ann_q": "当季年化贴水",
    "slope": "期限结构斜率（远-近）",
    "d_ann5": "贴水 5 日变化（当季年化）",
    "vol_ratio": "量能比（成交量/20 日均量）",
    "mom20": "20 日动量",
    "dev20": "距 20 日均线偏离",
    "hl20": "广度（20 日新高新低净值）",
}


def fmt(col: str, v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if col == "vol_ratio":
        return f"{v:.2f}×"
    if col == "d_ann5":
        return f"{v:+.2f} pt"
    return f"{v:+.2f}%"


def load_ref() -> pd.DataFrame:
    if not REF_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(REF_FILE)
    df["date"] = pd.to_datetime(df["date"])
    return df


def quantiles(ref: pd.DataFrame) -> dict[str, tuple[float, float]]:
    out = {}
    for col in FEATURE_LABELS:
        if col in ref.columns:
            s = ref[col].dropna()
            if len(s) >= 60:
                out[col] = (float(s.quantile(0.30)), float(s.quantile(0.70)))
    return out


def rank_pct(ref: pd.DataFrame, col: str, value, window: int = 250) -> float | None:
    if value is None or col not in ref.columns:
        return None
    s = ref[col].dropna().tail(window)
    if len(s) < 30:
        return None
    return float((s < value).mean() * 100)


# ------------------------------------------------------------
# 实时数据
# ------------------------------------------------------------
def fetch_ohlc(symbol: str) -> pd.DataFrame:
    import akshare as ak
    df = ak.index_zh_daily_sina(symbol=symbol)[["date", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_hl() -> pd.DataFrame:
    import akshare as ak
    df = ak.stock_a_high_low_statistics(symbol="all").copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def datetime_now():
    import market_core as mc
    return mc.now_cn()


def drop_unclosed_today(df: pd.DataFrame, now=None) -> pd.DataFrame:
    """15:05 前把当天还没收盘的日线去掉"""
    if df is None or df.empty:
        return df
    now = now or datetime_now()
    last = pd.to_datetime(df["date"].iloc[-1]).date()
    if last == now.date() and (now.hour * 60 + now.minute) < 15 * 60 + 5:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def merge_volume(sh: pd.DataFrame, sz: pd.DataFrame) -> pd.DataFrame:
    """两市成交量相加；只有一边有当天数据时（常见于深证日线更新晚）按内连接，避免算成缩量"""
    a = sh[["date", "volume"]].rename(columns={"volume": "sh"})
    b = sz[["date", "volume"]].rename(columns={"volume": "sz"})
    m = a.merge(b, on="date", how="inner").sort_values("date").reset_index(drop=True)
    m["vol"] = m["sh"] + m["sz"]
    return m[["date", "vol"]]


def build_live(detail: pd.DataFrame, hist_if: pd.DataFrame,
               ohlc_volume: pd.DataFrame, hl: pd.DataFrame) -> dict:
    """当日行情 + 历史序列 → 7 个特征"""
    import market_core as mc

    out = {}
    if detail is None or detail.empty:
        return out
    row = detail[detail["品种"] == "IF"].iloc[0]

    near_d = float(row["近月贴水率(%)"])
    q_d = float(row["当季贴水率(%)"])
    far_d = float(row["远月贴水率(%)"])
    dte = mc.days_left(str(row["近月合约"]))
    q_dte = mc.days_left(str(row["当季合约"]))

    out.update({
        "basis_near": near_d / 100,
        "basis_q": q_d / 100,
        "ann_q": float(row["当季年化(%)"]),
        "slope": (far_d - near_d) / (100 + near_d) * 100,
        "dte": dte, "q_dte": q_dte,
        "near_code": str(row["近月合约"]), "q_code": str(row["当季合约"]),
        "fut_date": str(row["数据日期"]),
    })

    if hist_if is not None and len(hist_if) >= 61:
        dcol = "日期" if "日期" in hist_if.columns else ("d" if "d" in hist_if.columns else hist_if.columns[0])
        px = pd.Series(hist_if["现货"].astype(float).values)
        out["close"] = float(px.iloc[-1])
        out["mom20"] = float((px.iloc[-1] / px.iloc[-21] - 1) * 100)
        out["dev20"] = float((px.iloc[-1] / px.tail(20).mean() - 1) * 100)
        out["as_of_price"] = pd.to_datetime(hist_if[dcol].iloc[-1]).date()

    if ohlc_volume is not None and len(ohlc_volume) >= 22:
        v = pd.Series(ohlc_volume["vol"].astype(float).values)
        out["vol_ratio"] = float(v.iloc[-1] / v.iloc[-21:-1].mean())
        out["vol_date"] = pd.to_datetime(ohlc_volume["date"].iloc[-1]).date()

    if hl is not None and not hl.empty:
        last = hl.iloc[-1]
        hi, lo = float(last["high20"]), float(last["low20"])
        out["hl20"] = (hi - lo) / (hi + lo) * 100 if (hi + lo) else None
        out["hl_date"] = pd.to_datetime(last["date"]).date()

    return out


def _hit(cond, value, th) -> bool:
    col, kind = cond
    if value is None or col not in th or (isinstance(value, float) and np.isnan(value)):
        return False
    lo, hi = th[col]
    return value <= lo if kind == "low" else value >= hi


def evaluate(live: dict, ref: pd.DataFrame) -> dict:
    th = quantiles(ref) if not ref.empty else {}
    live = dict(live)
    res = {"th": th, "ranks": {}, "rules": []}

    # d_ann5：参考表里往前推 5 个交易日
    if not ref.empty and "ann_q" in live and "ann_q" in ref.columns:
        past = ref["ann_q"].dropna().tail(6)
        if len(past) >= 6:
            live["d_ann5"] = float(live["ann_q"] - past.iloc[0])

    for col in FEATURE_LABELS:
        if col in live:
            res["ranks"][col] = rank_pct(ref, col, live[col])

    for r in RULES:
        hits = [_hit(c, live.get(c[0]), th) for c in r["conds"]]
        res["rules"].append({**r, "hits": hits, "on": all(hits)})

    strong = any(x["on"] for x in res["rules"] if x["key"] == "strong")
    main = any(x["on"] for x in res["rules"] if x["key"] == "main")
    avoid = any(x["on"] for x in res["rules"] if x["key"] == "avoid")
    bonus = any(x["on"] for x in res["rules"] if x["key"] == "bonus")

    if strong and main:
        tone, why = "强偏多", "贴水修复 + 广度强 + 放量动量同时成立，是历史上最稳的一档"
    elif strong:
        tone, why = "强偏多", "贴水 5 日修复 + 广度强（20 日胜率 69.2%、60 日 65.1%）"
    elif main and avoid:
        tone, why = "信号冲突", "放量动量偏多，但贴水修复 + 跌破均线是偏空组合，两边抵消，按震荡处理"
    elif main:
        tone, why = "偏多", "放量 + 动量为正（20 日胜率 66.4%，前后两段 67.3%/65.5%）"
    elif avoid:
        tone, why = "偏空", "贴水修复但指数仍在 20 日均线下方，历史 20 日胜率仅 36.2%"
    else:
        tone, why = "中性", "没有稳定条件命中，按震荡处理，不硬套方向"

    res.update({"tone": tone, "why": why, "bonus": bonus, "live": live})

    # 位置提示：多数指标落在近 250 日最低 20% 分位（只描述位置，不当方向信号）
    low_cols = ["ann_q", "vol_ratio", "mom20", "dev20", "hl20"]
    low_n = sum(1 for c in low_cols if (res["ranks"].get(c) or 100) < 20)
    res["weak_note"] = (f"{low_n} 个指标处于近 250 日最低 20% 分位，当前属弱势位置；"
                        "历史没有验证过的稳定偏空组合，所以不给方向，只提示别追多") if low_n >= 3 else ""
    return res


def stats_table(ev: dict) -> pd.DataFrame:
    """面板里展示的规则 & 历史表现"""
    rows = []
    for r in ev["rules"]:
        rows.append({
            "分层": r["name"],
            "当前": "命中" if r["on"] else "未命中",
            "样本": r["n"],
            "20日胜率": f"{r['win20']:.1f}%",
            "20日均值": f"{r['mean20']:+.2f}%",
            "60日胜率": f"{r['win60']:.1f}%",
            "60日均值": f"{r['mean60']:+.2f}%",
            "前后段": r["seg"],
            "说明": r["read"],
        })
    return pd.DataFrame(rows)
