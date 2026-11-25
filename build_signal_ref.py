# -*- coding: utf-8 -*-
"""重建信号体检参考表：用合约级缓存算稳定的「当季年化」，并复核规则胜率

相比 3 年报告里用的近月年化（临近交割会被放大、换月会跳），
面板改用当季合约年化（dte 50–150，跨月稳定），阈值与胜率一并重算。
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

CACHE = "data/backtest/multi_features.csv"
FUT = "data/backtest/fut_IF.csv"
OUT = "signal_ref.csv"


def expiry(code: str) -> date:
    y, m = 2000 + int(code[2:4]), int(code[4:6])
    first = date(y, m, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7) + timedelta(days=14)


feat = pd.read_csv(CACHE)
feat["date"] = pd.to_datetime(feat["date"])

fut = pd.read_csv(FUT)
fut["date"] = pd.to_datetime(fut["date"])
fut["exp"] = fut["code"].map(lambda c: pd.Timestamp(expiry(c)))
fut["dte"] = (fut["exp"] - fut["date"]).dt.days

rows = []
for d, g in fut.groupby("date"):
    live = g[g["dte"] >= 1].sort_values("dte")
    if live.empty:
        continue
    near = live.iloc[0]
    far = live.iloc[-1]
    q = live[(live["dte"] >= 50) & (live["dte"] <= 150)]
    if q.empty:
        q = live[live["dte"] >= 20]
    if q.empty:
        continue
    qrow = q.sort_values("hold", ascending=False).iloc[0]
    rows.append({"date": d, "near": near["close"], "near_dte": near["dte"],
                 "q": qrow["close"], "q_dte": qrow["dte"], "far": far["close"]})

cf = pd.DataFrame(rows)
feat = feat.drop(columns=["near", "far", "dte", "ann", "slope", "d_ann5", "basis"],
                 errors="ignore")
df = feat.merge(cf, on="date", how="left")

df["basis"] = df["near"] / df["close"] - 1                       # 近月贴水率（小数）
df["basis_q"] = df["q"] / df["close"] - 1                        # 当季贴水率（小数）
df["ann_q"] = df["basis_q"] / df["q_dte"] * 365 * 100            # 当季年化（%）
df["ann"] = df["basis"] / df["near_dte"].clip(lower=1) * 365 * 100  # 近月年化（对照用）
df["slope"] = (df["far"] - df["near"]) / df["near"] * 100        # 期限结构（远-近）
df["d_ann5"] = df["ann_q"] - df["ann_q"].shift(5)                # 5日当季年化变化

cols = ["date", "close", "near", "q", "q_dte", "far", "basis", "basis_q", "ann", "ann_q",
        "d_ann5", "slope", "vol_ratio", "mom20", "mom60", "dev20", "hl20",
        "fwd_5", "fwd_20", "fwd_60"]
out = df[cols].copy()
out["date"] = out["date"].dt.strftime("%Y-%m-%d")
out.to_csv(OUT, index=False, encoding="utf-8-sig")
print(f"已写出 {OUT}  {out.shape}  {out.date.min()} ~ {out.date.max()}")
print(f"当季年化可用 {df['ann_q'].notna().sum()} 行；广度可用 {df['hl20'].notna().sum()} 行")

# ---------------- 复核规则（阈值 = 全样本 30/70 分位） ----------------
base_up = (df.fwd_20 > 0).mean() * 100
base_mean = df.fwd_20.mean()
print(f"\n基准 20 日胜率 {base_up:.1f}%  均值 {base_mean:+.2f}%  样本 {len(df)}")

QCOLS = ["ann_q", "d_ann5", "slope", "vol_ratio", "mom20", "dev20", "hl20"]
q = {c: (df[c].quantile(0.30), df[c].quantile(0.70)) for c in QCOLS}
print("\n30% / 70% 分位阈值：")
for k, (lo, hi) in q.items():
    print(f"  {k:10s} 低 {lo:+.4f}  高 {hi:+.4f}")

cv = df["date"].quantile(0.55)


def rule(name, mask):
    g = df[mask.fillna(False)]
    if len(g) < 30:
        print(f"\n{name}: 样本仅 {len(g)}，跳过")
        return
    v20, v60 = g.fwd_20.dropna(), g.fwd_60.dropna()
    a = g[g.date <= cv].fwd_20.dropna()
    b = g[g.date > cv].fwd_20.dropna()
    print(f"\n{name}\n  样本 {len(g)} | 20日 胜率 {100*(v20>0).mean():.1f}% 均值 {v20.mean():+.2f}%"
          f" | 60日 胜率 {100*(v60>0).mean():.1f}% 均值 {v60.mean():+.2f}%"
          f"\n  前段 {100*(a>0).mean():.1f}% ({len(a)}) / 后段 {100*(b>0).mean():.1f}% ({len(b)})")


rule("① 放量 + 动量为正", (df.vol_ratio >= q["vol_ratio"][1]) & (df.mom20 >= q["mom20"][1]))
rule("② 深贴水(当季) + 广度强", (df.ann_q <= q["ann_q"][0]) & (df.hl20 >= q["hl20"][1]))
rule("③ 深贴水(当季) + 放量", (df.ann_q <= q["ann_q"][0]) & (df.vol_ratio >= q["vol_ratio"][1]))
rule("④ 当季年化5日加深 + 放量", (df.d_ann5 <= q["d_ann5"][0]) & (df.vol_ratio >= q["vol_ratio"][1]))
rule("⑤ 回避: 贴水收敛(当季) + 缩量", (df.ann_q >= q["ann_q"][1]) & (df.vol_ratio <= q["vol_ratio"][0]))
rule("⑥ 贴水收敛单条(当季)", df.ann_q >= q["ann_q"][1])
rule("⑦ 深贴水 + 广度强 + 放量",
     (df.ann_q <= q["ann_q"][0]) & (df.hl20 >= q["hl20"][1]) & (df.vol_ratio >= q["vol_ratio"][1]))
rule("⑧ 广度强 + 动量为正", (df.hl20 >= q["hl20"][1]) & (df.mom20 >= q["mom20"][1]))
rule("⑨ 广度强 + 放量 + 动量为正",
     (df.hl20 >= q["hl20"][1]) & (df.vol_ratio >= q["vol_ratio"][1]) & (df.mom20 >= q["mom20"][1]))
