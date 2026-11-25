# -*- coding: utf-8 -*-
"""
A股市场阶段分析 · 网页版 v4
版块顺序按重要性排列：市场广度与量能 → 龙头 ETF 资金 → 股指期货基差 → 外围
- 历史：每日快照自动落盘（CSV + Excel，含「盘面简述」）

抓取与计算逻辑全部在 market_core.py，网页与定时脚本共用同一套口径。
"""

from datetime import date, datetime
import os
import re

import altair as alt
import pandas as pd
import streamlit as st

import market_core as mc

# ============================================================
# 0. 页面配置
# ============================================================
st.set_page_config(
    page_title="A股市场阶段分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _secret(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val:
        return val
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


def password_gate() -> None:
    """设置了口令才启用（云端公开链接用），没设置就完全跳过"""
    pwd = _secret("APP_PASSWORD")
    if not pwd or st.session_state.get("_pwd_ok"):
        return

    st.markdown("#### 🔒 请输入访问口令")
    v = st.text_input("口令", type="password", label_visibility="collapsed")
    if v:
        if v == pwd:
            st.session_state["_pwd_ok"] = True
            st.rerun()
        st.error("口令不对，再试一次")
    st.stop()


password_gate()

HIST_CSV, HIST_XLSX = mc.HIST_CSV, mc.HIST_XLSX

# 抓取函数包一层缓存（内核本身不带缓存，方便定时脚本直接调用）
@st.cache_data(ttl=120, show_spinner=False)
def load_spot_index():
    return mc.load_spot_index()


@st.cache_data(ttl=60, show_spinner=False)
def load_futures():
    return mc.load_futures()


@st.cache_data(ttl=120, show_spinner=False)
def load_breadth():
    return mc.load_breadth()


@st.cache_data(ttl=600, show_spinner=False)
def load_breadth_light():
    return mc.load_breadth_light()


@st.cache_data(ttl=1800, show_spinner=False)
def load_discount_history(code: str, window: int):
    return mc.load_discount_history(code, window)


@st.cache_data(ttl=300, show_spinner=False)
def load_overseas():
    return mc.load_overseas()


@st.cache_data(ttl=120, show_spinner=False)
def load_etf_quotes():
    return mc.load_etf_quotes()


@st.cache_data(ttl=1800, show_spinner=False)
def load_etf_scale(n_days: int = 8):
    return mc.load_etf_scale(n_days)


def cn_line_chart(df: pd.DataFrame, col: str, color: str, height: int = 220, y_title: str | None = None):
    """折线图，横轴月份用中文（2026年07月）"""
    d = df.copy()
    d["日期"] = pd.to_datetime(d["日期"])
    chart = (
        alt.Chart(d)
        .mark_line(color=color)
        .encode(
            x=alt.X("日期:T", title=None,
                    axis=alt.Axis(format="%Y年%m月", tickCount=6, labelAngle=0, grid=False)),
            y=alt.Y(f"{col}:Q", title=y_title or col,
                    axis=alt.Axis(format=".2f", titleFontSize=11, labelFontSize=10)),
            tooltip=[alt.Tooltip("日期:T", title="日期", format="%Y-%m-%d"),
                     alt.Tooltip(f"{col}:Q", title=col, format=".2f")],
        )
        .properties(height=height, width="container")
    )
    st.altair_chart(chart)


def mcard(col, label: str, value: str, sub: str = "", metric: str | None = None,
          raw=None, color: str | None = None):
    """彩色指标卡：红=利好、绿=利空，深红/深绿=程度更重"""
    hexc = color or (mc.color_of(metric, raw) if (metric and raw is not None) else None)
    v_style = f"color:#{hexc}" if hexc else "color:#111827"
    s_style = f"color:#{hexc}" if hexc else "color:#6b7280"
    col.markdown(
        f'<div class="mcard"><div class="mlabel">{label}</div>'
        f'<div class="mval" style="{v_style}">{value}</div>'
        f'<div class="msub" style="{s_style}">{sub if sub else "&nbsp;"}</div></div>',
        unsafe_allow_html=True,
    )


def col_style(col: pd.Series) -> list[str]:
    """pandas Styler 用：按列名套用利好利空配色"""
    name = str(col.name)
    return [mc.style_of(name, v) for v in col]


# ============================================================
# 1. 样式与标题
# ============================================================
st.markdown(
    """
<style>
    .block-container {padding-top: 1.3rem; padding-bottom: 2.4rem; max-width: 1260px;}
    .concl {border-left:5px solid #2563eb; background:#f8fafc; padding:16px 20px; border-radius:10px; line-height:1.9;}
    .mcard {background:#ffffff; border:1px solid #e5e7eb; border-radius:10px;
            padding:10px 12px 9px; box-shadow:0 1px 2px rgba(16,24,40,.04);}
    .mlabel {font-size:12.5px; color:#6b7280; line-height:1.3; margin-bottom:2px;}
    .mval {font-size:23px; font-weight:650; line-height:1.25; letter-spacing:-.2px;}
    .msub {font-size:12px; color:#6b7280; line-height:1.35; margin-top:3px; min-height:16px;}
</style>
""",
    unsafe_allow_html=True,
)

st.title("📊 A股市场阶段分析")
st.caption("短线：市场广度与量能　|　现货：龙头宽基 ETF 净申购　|　中期：期指基差　|　外围：纳指期货　|　数据源 AKShare")

with st.sidebar:
    st.header("设置")
    window = st.select_slider("历史分位窗口（交易日）", options=[60, 120, 250, 500], value=250)
    auto_save = st.toggle("自动记录今日快照", value=True,
                          help="每次打开页面把当日关键指标写入 历史记录/市场阶段历史.csv 与 .xlsx，同一天只保留最新一条")
    if st.button("🔄 刷新数据", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.markdown("**术语速查**")
    st.markdown(
        "- **宽广度** = 上涨家数 ÷ 涨跌家数合计\n"
        "- **TRIN（阿姆士指标）** = (涨家数/跌家数) ÷ (涨成交额/跌成交额)\n"
        "- **额价差** = 上涨额占比 − 上涨家数占比\n"
        "- **ETF 份额变化** 增加＝净申购（真有资金进来），减少＝净赎回\n"
        "- **基差** = 期货价 − 现货指数价，负为贴水\n"
        "- **贴水率** = 基差 ÷ 现货点位（不年化）\n"
        "- **年化基差率** 折算成一年，越临近交割被放大越厉害\n"
        "- **当季合约** 距交割 2–4 个月，年化更稳定"
    )
    st.divider()
    st.caption("仅供研究参考，不构成投资建议。")

# ============================================================
# 2. 加载数据
# ============================================================
with st.spinner("正在抓取数据…"):
    fut_raw, spot_df = load_futures(), load_spot_index()

spot_price, sh_px, sh_chg = mc.extract_spot(spot_df)

try:
    overseas = load_overseas()
except Exception:
    overseas = {}

detail = mc.build_detail(fut_raw, spot_price)

try:
    breadth = load_breadth()
except Exception as e:
    try:
        breadth = load_breadth_light()
        st.warning(f"全市场快照失败（{type(e).__name__}），已降级：涨跌家数准确、量能指标缺失。")
    except Exception as e2:
        breadth = None
        st.error(f"广度数据获取失败：{e2}")

if breadth and not mc.breadth_ok(breadth):
    breadth = None

etf_table, etf_sum = pd.DataFrame(), None
etf_note = ""
try:
    quotes, scale = load_etf_quotes(), load_etf_scale(8)
    etf_table, etf_sum, etf_note = mc.build_etf(quotes, scale)
except Exception as e:
    etf_note = f"ETF 数据获取失败：{type(e).__name__}"

verdict = mc.market_verdict(detail, breadth, etf_sum, overseas)

# ============================================================
# 3. 今日一句话
# ============================================================
if verdict:
    st.info(f"**今日盘面**：{verdict}")

# ============================================================
# 4. 一、市场广度与量能（短线）
# ============================================================
if breadth:
    st.markdown("### 一、市场广度与量能（短线位置）")
    c1, c2, c3, c4, c5 = st.columns(5)
    mcard(c1, "上涨 / 下跌", f"{breadth['up']} / {breadth['down']}",
          metric="市场宽广度(%)", raw=breadth["breadth"])
    mcard(c2, "市场宽广度", f"{breadth['breadth']:.1f}%",
          metric="市场宽广度(%)", raw=breadth["breadth"],
          sub=mc.metric_level("市场宽广度(%)", breadth["breadth"]))
    mcard(c3, "阿姆士 TRIN", "—" if breadth["trin"] is None else f"{breadth['trin']:.2f}",
          metric="TRIN", raw=breadth["trin"],
          sub="高于 1.2＝量能偏空" if (breadth["trin"] or 0) > 1.2 else "低于 1.0＝量能偏多")
    mcard(c4, "上涨额占比", "—" if breadth["amt_share"] is None else f"{breadth['amt_share']:.1f}%",
          metric="上涨额占比(%)", raw=breadth["amt_share"],
          sub="资金偏权重" if (breadth["amt_share"] or 50) > 55 else "资金偏小票")
    mcard(c5, "两市成交额", "—" if breadth["total_amt"] is None else f"{breadth['total_amt']:.0f} 亿")

    level, why = mc.short_term_level(breadth["breadth"], breadth["trin"])
    st.success(f"**短线判断：{level}** — {why}")

    extra = f"涨停 {breadth['limit_up']} 家 / 跌停 {breadth['limit_down']} 家"
    if breadth["amt_spread"] is not None:
        extra += f"　|　额价差 {breadth['amt_spread']:+.1f}%（正值＝资金偏权重，负值＝资金偏小票）"
    st.caption(f"{extra}　|　口径：{breadth['source']}　|　颜色＝红利好 / 绿利空 / 深色＝程度更重")
else:
    st.markdown("### 一、市场广度与量能（短线位置）")
    st.warning("广度量能暂无可信数据：可能还没开盘、或接口刚开盘尚未更新。开盘后点侧边栏「刷新数据」即可。")

# ============================================================
# 5. 二、现货端 ETF
# ============================================================
st.markdown("### 二、现货端 · 龙头宽基 ETF 份额与成交")

if etf_sum is None:
    st.warning("ETF 数据暂未取到。" + etf_note)
else:
    c1, c2, c3, c4 = st.columns(4)
    mcard(c1, "最新份额日期", str(etf_sum["date"]),
          sub="T+1 发布，今日数据明日可查" if etf_sum["date"] != mc.today_cn() else "")
    mcard(c2, "当日合计净申赎", f"{etf_sum['day_sum']:+.1f} 亿",
          metric="ETF当日净申赎(亿)", raw=etf_sum["day_sum"],
          sub=f"对比 {etf_sum['prev']}" if etf_sum["prev"] else "")
    mcard(c3, "近5日累计净申赎", f"{etf_sum['sum5']:+.1f} 亿",
          metric="ETF近5日净申赎(亿)", raw=etf_sum["sum5"])
    mcard(c4, "当日合计成交额", f"{etf_sum['amt_sum']:.1f} 亿")

    st.success(f"**ETF 资金信号：{mc.etf_signal_text(etf_sum['day_sum'])}** — "
               f"{etf_sum['count']} 只龙头宽基合计当日净申赎 {etf_sum['day_sum']:+.1f} 亿元。")

    tbl = etf_table.copy()
    st.dataframe(
        etf_table.style
        .apply(col_style, subset=["涨跌幅(%)", "日净申赎(亿元)", "近5日净申赎(亿元)"], axis=0)
        .format({"最新价": "{:.3f}", "涨跌幅(%)": "{:+.2f}", "成交额(亿)": "{:.2f}",
                 "份额(亿份)": "{:.1f}", "日增份额(亿份)": "{:+.2f}",
                 "日净申赎(亿元)": "{:+.1f}", "近5日净申赎(亿元)": "{:+.1f}"}),
        width="stretch", hide_index=True,
    )
    st.caption(
        "口径：上交所披露的 ETF 基金份额（份）。份额增加＝净申购（真的有资金进来），减少＝净赎回。"
        "净申赎金额 ≈ 份额变化 × 最新价，用于横向比较不同 ETF 的资金体量；"
        "份额数据 T+1 发布，页面显示的是最近可取到的交易日。"
    )

# ============================================================
# 6. 三、中期期指
# ============================================================
hist_info = {}
st.markdown("### 三、中期情绪 · 股指期货基差")

if not detail.empty:
    if_row = detail[detail["品种"] == "IF"].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    mcard(c1, "IF 近月合约", if_row["近月合约"],
          metric="近月贴水率(%)", raw=if_row["近月贴水率(%)"],
          sub=f"贴水 {if_row['近月基差点数']} 点")
    mcard(c2, "IF 当季年化基差", f"{if_row['当季年化(%)']}%",
          metric="IF当季年化(%)", raw=if_row["当季年化(%)"],
          sub=f"当季 {if_row['当季合约']}")
    mcard(c3, "IF 中期情绪", if_row["情绪"], color=mc.mood_color(if_row["情绪"]))
    mcard(c4, "数据日期", if_row["数据日期"])

    show = detail[["品种", "指数", "近月合约", "近月基差点数", "近月贴水率(%)",
                   "当季合约", "当季贴水率(%)", "当季年化(%)",
                   "远月合约", "远月贴水率(%)", "期限形态", "持仓量", "情绪"]]
    styler = (
        show.style
        .apply(col_style, subset=["近月贴水率(%)", "当季贴水率(%)", "当季年化(%)",
                                  "远月贴水率(%)", "期限形态"], axis=0)
        .apply(col_style, subset=["情绪"], axis=0)
        .format({"近月贴水率(%)": "{:.2f}", "当季贴水率(%)": "{:.2f}",
                 "当季年化(%)": "{:.1f}", "远月贴水率(%)": "{:.2f}"})
    )
    st.dataframe(styler, width="stretch", hide_index=True)

    big = detail[detail["风格"] == "大盘"]["当季年化(%)"].mean()
    small = detail[detail["风格"] == "中小盘"]["当季年化(%)"].mean()
    st.info(f"风格对比：大盘（IH/IF）当季年化 {big:.1f}%，中小盘（IC/IM）当季年化 {small:.1f}%。"
            + ("中小盘贴水更深，对冲与小票抛压更集中。" if small < big else "大盘贴水更深，权重端对冲压力更大。"))

    st.markdown(f"#### 近月贴水率历史分位（近 {window} 个交易日，未年化）")
    hcols = st.columns(4)
    for i, code in enumerate(["IH", "IF", "IC", "IM"]):
        try:
            hist = load_discount_history(code, window)
            now_disc = float(detail[detail["品种"] == code].iloc[0]["近月贴水率(%)"])
            pct = float((hist["贴水率(%)"] < now_disc).mean() * 100)
            hist_info[code] = (now_disc, pct, hist)
            mcard(hcols[i], f"{code} 近月贴水率", f"{now_disc:.2f}%",
                  metric="近月贴水率(%)", raw=now_disc,
                  sub=f"历史分位 {pct:.0f}%")
        except Exception:
            pass

    if "IF" in hist_info:
        hist = hist_info["IF"][2].copy()
        hist["日期"] = pd.to_datetime(hist["d"])
        cn_line_chart(hist, "贴水率(%)", "#2563eb", height=220, y_title="贴水率（%）")
        st.caption("IF 主力连续合约贴水率走势（负值越深＝折价越大）。近月贴水率未年化，不受临近交割放大影响。")
else:
    st.warning("期指数据未取到，请点侧边栏「刷新数据」重试。")

# ============================================================
# 7. 四、外围与大盘
# ============================================================
st.markdown("### 四、外围与大盘")

c1, c2, c3, c4 = st.columns(4)
nq = overseas.get("NQ")
if nq:
    mcard(c1, f"纳指期货 NQ（{nq['date']}）", f"{nq['price']:,.0f}",
          metric="纳指期货涨跌幅(%)", raw=nq["chg"], sub=f"{nq['chg']:+.2f}%")
else:
    mcard(c1, "纳指期货 NQ", "—", sub="数据未取到")

ixic = overseas.get("IXIC")
if ixic:
    mcard(c2, f"纳斯达克指数（{ixic['date']}）", f"{ixic['price']:,.0f}",
          metric="纳斯达克指数涨跌幅(%)", raw=ixic["chg"], sub=f"{ixic['chg']:+.2f}%")
else:
    mcard(c2, "纳斯达克指数", "—", sub="数据未取到")

if sh_px is not None:
    mcard(c3, "上证指数", f"{sh_px:,.2f}",
          metric="上证涨跌幅(%)", raw=sh_chg,
          sub=f"{sh_chg:+.2f}%" if sh_chg is not None else "")
else:
    mcard(c3, "上证指数", "—", sub="数据未取到")

es = overseas.get("ES")
if es:
    mcard(c4, f"标普期货 ES（{es['date']}）", f"{es['price']:,.0f}",
          metric="纳指期货涨跌幅(%)", raw=es["chg"], sub=f"{es['chg']:+.2f}%")
else:
    mcard(c4, "标普期货 ES", "—", sub="数据未取到")

st.caption(
    "口径：纳指期货/标普期货取外盘日线最新一根（含亚洲时段报价），涨跌幅对比前一交易日收盘；"
    "纳斯达克指数为美股最近一个交易日收盘；上证指数为A股当日实时。"
)

# ============================================================
# 8. 五、综合
# ============================================================
st.markdown("### 五、综合阶段判断")
lines = []

if breadth:
    level, why = mc.short_term_level(breadth["breadth"], breadth["trin"])
    t = f"**短线**：宽广度 {breadth['breadth']:.1f}%"
    if breadth["trin"] is not None:
        t += f"、TRIN {breadth['trin']:.2f}"
    t += f"，处于【{level}】。{why}。"
    lines.append(t)

if etf_sum is not None:
    txt = (f"**现货端**：{etf_sum['count']} 只龙头宽基 ETF 在 {etf_sum['date']} 合计净申赎 "
           f"{etf_sum['day_sum']:+.1f} 亿元（近5日 {etf_sum['sum5']:+.1f} 亿元）。")
    if etf_sum["day_sum"] > mc.ETF_SIGNAL["强净申购"]:
        txt += "达到百亿级净申购，是“真有资金进来”的现货证据，比期指更能定位护盘痕迹；但需与指数位置、政策面一起判断，单日大额申购也可能来自机构配置或套利。"
    elif etf_sum["day_sum"] > mc.ETF_SIGNAL["净申购"]:
        txt += "为温和净申购，资金偏正面但未到强介入程度。"
    elif etf_sum["day_sum"] < mc.ETF_SIGNAL["中性"]:
        txt += "为净赎回，现货端资金在流出，与期指贴水共同指向偏弱。"
    else:
        txt += "申赎基本平衡，现货端无明显方向。"
    lines.append(txt)

if not detail.empty:
    if_row = detail[detail["品种"] == "IF"].iloc[0]
    txt = (f"**中期**：沪深300 期限结构为近月 {if_row['近月贴水率(%)']}% → 当季 {if_row['当季贴水率(%)']}% "
           f"→ 远月 {if_row['远月贴水率(%)']}%，当季 {if_row['当季合约']} 年化基差 {if_row['当季年化(%)']}%，"
           f"落在【{if_row['情绪']}】区间。")
    shape = str(if_row["期限形态"])
    txt += ("远月折价明显深于近月，说明市场对中期定价更谨慎、对冲盘集中在远月。" if "更深" in shape
            else "远月折价浅于近月，期限曲线在修复，中期预期边际改善。" if "修复" in shape
            else "期限曲线相对平坦，远期与近期定价分歧不大。")
    pct = hist_info.get("IF", (None, None, None))[1]
    if pct is not None:
        txt += f"（近月折价处于近 {window} 日 {pct:.0f}% 分位"
        txt += "，即近期折价并不极端，悲观主要体现在远月。）" if pct < 60 else "，近期折价也已进入历史偏深区间。）"
    lines.append(txt)

if overseas or sh_chg is not None:
    parts = []
    if sh_px is not None and sh_chg is not None:
        parts.append(f"上证 {sh_px:,.0f} 点（{sh_chg:+.2f}%）")
    if "IXIC" in overseas:
        parts.append(f"纳斯达克指数上日 {overseas['IXIC']['chg']:+.2f}%")
    if "NQ" in overseas:
        parts.append(f"纳指期货 {overseas['NQ']['chg']:+.2f}%")
    txt = "**外围**：" + "，".join(parts) + "。"
    if "NQ" in overseas and "IXIC" in overseas:
        nq_chg, ix_chg = overseas["NQ"]["chg"], overseas["IXIC"]["chg"]
        if nq_chg > 1 and ix_chg < -1:
            txt += "隔夜美股明显下跌，但纳指期货在盘后/亚洲时段反弹超 1%，说明恐慌情绪正在修复，对A股风险偏好偏正面。"
        elif nq_chg < -1:
            txt += "纳指期货继续走弱，外围风险偏好承压，对A股开盘情绪不利。"
        elif nq_chg > 1:
            txt += "纳指期货走强，外围风险偏好在改善。"
        else:
            txt += "纳指期货波动不大，外围对A股情绪影响中性。"
    lines.append(txt)

st.markdown(
    '<div class="concl">' + re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", "<br><br>".join(lines)) + "</div>",
    unsafe_allow_html=True,
)

# ============================================================
# 9. 六、信号体检（三年回测条件）
# ============================================================
st.markdown("### 六、信号体检（近三年回测条件）")


@st.cache_data(ttl=1800, show_spinner=False)
def load_index_ohlc(symbol: str):
    import signal_panel as sp
    return sp.fetch_ohlc(symbol)


@st.cache_data(ttl=1800, show_spinner=False)
def load_hl_stat():
    import signal_panel as sp
    return sp.fetch_hl()


@st.cache_data(ttl=1800, show_spinner=False)
def load_if_hist_300():
    return mc.load_discount_history("IF", 300)


try:
    import signal_panel as sp

    vol = sp.merge_volume(
        sp.drop_unclosed_today(load_index_ohlc("sh000001")),
        sp.drop_unclosed_today(load_index_ohlc("sz399001")),
    )
    live = sp.build_live(detail, load_if_hist_300(), vol, load_hl_stat())
    ev = sp.evaluate(live, sp.load_ref())

    tone_hex = {"强偏多": mc.BULL_STRONG, "偏多": mc.BULL, "中性": mc.NEUTRAL,
                "信号冲突": "B45309", "偏空": mc.BEAR, "强偏空": mc.BEAR_STRONG}[ev["tone"]]

    cols7 = st.columns(4)
    for c, col_name, label in [
        (cols7[0], "ann_q", "当季年化贴水"),
        (cols7[1], "vol_ratio", "量能比（成交量/20日均量）"),
        (cols7[2], "mom20", "沪深300 20 日动量"),
        (cols7[3], "hl20", "广度（20日新高新低净值）"),
    ]:
        rk = ev["ranks"].get(col_name)
        mcard(c, label, sp.fmt(col_name, ev["live"].get(col_name)),
              sub=f"近 250 日 {rk:.0f}% 分位" if rk is not None else "分位不可用")

    st.markdown(
        f'<div class="concl" style="border-left-color:#{tone_hex}">'
        f'<strong style="color:#{tone_hex};font-size:1.05rem">体检结论：{ev["tone"]}</strong><br><br>'
        f'{ev["why"]}。{"（另有加分条件命中：广度强 + 放量 + 动量为正。）" if ev["bonus"] else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.dataframe(sp.stats_table(ev), width="stretch", hide_index=True,
                 column_config={"说明": st.column_config.TextColumn(width="large")})

    st.caption(
        f"口径：阈值 = 近三年（{len(sp.load_ref())} 个交易日）全样本 30%/70% 分位；"
        "贴水一律用当季合约年化（dte 50–150），不用近月——近月年化在交割周会被机械放大，"
        "历史统计因此会虚高。各指标取数日："
        f"价格 {live.get('as_of_price')}、量能 {live.get('vol_date')}、"
        f"广度 {live.get('hl_date')}、期指 {live.get('fut_date')}。"
        "样本内挖掘 + 概率偏好，不是买卖点。"
    )
except Exception as e:
    st.warning(f"信号体检暂不可用：{type(e).__name__} {e}")

# ============================================================
# 10. 七、历史记录
# ============================================================
st.markdown("### 七、每日历史记录")

snap = mc.build_snapshot(detail, breadth, etf_sum, overseas, sh_chg)

if auto_save and snap is not None:
    try:
        mc.save_snapshot(snap)
        if mc._github_backend():
            where = "GitHub 仓库里的 `history.csv`"
        elif mc._feishu_backend():
            where = "飞书多维表格"
        else:
            where = f"`{HIST_CSV.name}` / `{HIST_XLSX.name}`"
        st.caption(f"✅ 今日快照已写入：{where}（同一天重复打开只保留最新一条）")
    except Exception as e:
        st.warning(f"写入历史记录失败：{e}")
elif not auto_save:
    if st.button("记录 / 更新今日快照"):
        try:
            mc.save_snapshot(snap)
            st.success("已写入历史记录")
        except Exception as e:
            st.error(f"写入失败：{e}")

hist_df = mc.load_history()
if not hist_df.empty:
    show_hist = hist_df.tail(20).iloc[::-1]
    hist_styler = show_hist.style.apply(col_style, axis=0).format(
        {"IF近月贴水率(%)": "{:.2f}", "IF当季年化(%)": "{:.2f}",
         "IH近月贴水率(%)": "{:.2f}", "IC近月贴水率(%)": "{:.2f}", "IM近月贴水率(%)": "{:.2f}",
         "市场宽广度(%)": "{:.2f}", "TRIN": "{:.2f}", "上涨额占比(%)": "{:.1f}",
         "两市成交额(亿)": "{:.0f}", "ETF当日净申赎(亿)": "{:+.1f}",
         "ETF近5日净申赎(亿)": "{:+.1f}", "ETF合计成交额(亿)": "{:.1f}",
         "纳指期货点位": "{:.0f}", "纳指期货涨跌幅(%)": "{:+.2f}",
         "纳斯达克指数涨跌幅(%)": "{:+.2f}", "上证涨跌幅(%)": "{:+.2f}"},
        na_rep="—",
    )
    st.dataframe(hist_styler, width="stretch", hide_index=True)

    cc1, cc2 = st.columns(2)
    with cc1:
        csv_bytes = hist_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ 下载 CSV", csv_bytes,
                           file_name="市场阶段历史.csv", mime="text/csv", width="stretch")
    with cc2:
        try:
            import io
            buf = io.BytesIO()
            hist_df.to_excel(buf, index=False, sheet_name="日度记录")
            st.download_button("⬇️ 下载 Excel", buf.getvalue(),
                               file_name="市场阶段历史.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")
        except Exception:
            pass

    chart_df = hist_df.copy()
    chart_df["日期"] = pd.to_datetime(chart_df["日期"], errors="coerce")
    chart_df = chart_df.dropna(subset=["日期"]).sort_values("日期").tail(60)

    ch1, ch2 = st.columns(2)
    with ch1:
        if "IF近月贴水率(%)" in chart_df.columns:
            st.markdown("**IF 近月贴水率（近 60 个记录）**")
            cn_line_chart(chart_df, "IF近月贴水率(%)", "#2563eb", height=200, y_title="贴水率（%）")
    with ch2:
        if "市场宽广度(%)" in chart_df.columns:
            st.markdown("**市场宽广度（近 60 个记录）**")
            cn_line_chart(chart_df, "市场宽广度(%)", "#059669", height=200, y_title="宽广度（%）")
else:
    st.caption("还没有历史记录，打开页面后会自动写入第一条。")

# ============================================================
# 10. 阅读指引
# ============================================================
with st.expander("怎么看这份结论（使用要点）", expanded=False):
    st.markdown(
        """
1. **先看背离，再看绝对值**：普跌但涨股放量＝超跌反弹条件；普涨但跌股放量＝冲高回落风险。这类信号只管 1–3 天，不是反转信号。
2. **现货看 ETF 份额，不看成交额**：成交额是买卖对撞，可被套利和调仓放大；份额净申购才是资金真正进场。几只龙头宽基同日大额净申购，是最接近“护盘资金”的公开证据。
3. **中期看基差，优先当季合约**：年化贴水越深、历史分位越高，说明对冲与做空压力越大。近月合约临近交割时年化会被机械放大，不宜单独当信号。
4. **外围看背离**：隔夜纳指大跌、次日期货反弹，是恐慌修复；两者同向下跌才是真压制。
5. **交叉验证**：ETF 净申购 + 期指贴水收窄 + 指数企稳，三者同向时可信度最高；只有其一，容易误判。
6. **分红季要修正**：5–7 月成分股分红会让贴水被动加深，需看分红调整口径。
        """
    )

st.divider()
st.caption(f"更新时间 {mc.now_cn().strftime('%Y-%m-%d %H:%M:%S')}（北京时间）　·　非交易时段显示最近交易日数据"
           "　·　仅供研究参考，不构成任何投资建议")
