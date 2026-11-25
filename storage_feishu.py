# -*- coding: utf-8 -*-
"""
飞书多维表格存储后端。

用途：Streamlit Cloud / GitHub Actions 这类云端环境磁盘是临时的，
历史记录改存到飞书多维表格（Bitable），手机也能直接打开表格看。

需要四个环境变量（或 Streamlit secrets）：
    FEISHU_APP_ID      自建应用 App ID
    FEISHU_APP_SECRET  自建应用 App Secret
    FEISHU_APP_TOKEN   多维表格的 app_token（base 链接里 bcnxxxx 那个）
    FEISHU_TABLE_ID    数据表 table_id（tblxxxx 那个）
"""

import math
import os
import time
from datetime import date, datetime, timezone, timedelta

import pandas as pd
import requests

BASE_URL = "https://open.feishu.cn/open-apis"
CN_TZ = timezone(timedelta(hours=8))

# Bitable 字段类型
FT_TEXT, FT_NUMBER, FT_DATE = 1, 2, 5

_token_cache = {"token": None, "expire_at": 0.0}
_fields_cache = {"ts": 0.0, "meta": None}


class FeishuError(RuntimeError):
    pass


# ============================================================
# 配置
# ============================================================
def _cfg() -> dict:
    return {
        "app_id": os.environ.get("FEISHU_APP_ID", "").strip(),
        "app_secret": os.environ.get("FEISHU_APP_SECRET", "").strip(),
        "app_token": os.environ.get("FEISHU_APP_TOKEN", "").strip(),
        "table_id": os.environ.get("FEISHU_TABLE_ID", "").strip(),
    }


def configured() -> bool:
    c = _cfg()
    return all(c.values())


# ============================================================
# 鉴权
# ============================================================
def _tenant_token(force: bool = False) -> str:
    if not force and _token_cache["token"] and time.time() < _token_cache["expire_at"]:
        return _token_cache["token"]

    c = _cfg()
    if not (c["app_id"] and c["app_secret"]):
        raise FeishuError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    r = requests.post(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": c["app_id"], "app_secret": c["app_secret"]},
        timeout=15,
    )
    js = r.json()
    if js.get("code") != 0:
        raise FeishuError(f"取 token 失败：{js.get('code')} {js.get('msg')}")

    _token_cache["token"] = js["tenant_access_token"]
    _token_cache["expire_at"] = time.time() + max(int(js.get("expire", 7200)) - 120, 60)
    return _token_cache["token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_tenant_token()}",
            "Content-Type": "application/json; charset=utf-8"}


def _check(js: dict, what: str) -> dict:
    if js.get("code") != 0:
        raise FeishuError(f"{what}失败：{js.get('code')} {js.get('msg')}")
    return js.get("data", {}) or {}


# ============================================================
# 字段元信息（决定怎么写值）
# ============================================================
def fields_meta(refresh: bool = False) -> dict:
    if not refresh and _fields_cache["meta"] and time.time() - _fields_cache["ts"] < 600:
        return _fields_cache["meta"]

    c = _cfg()
    url = f"{BASE_URL}/bitable/v1/apps/{c['app_token']}/tables/{c['table_id']}/fields"
    r = requests.get(url, headers=_headers(), params={"page_size": 100}, timeout=20)
    data = _check(r.json(), "取字段列表")

    meta = {}
    for it in data.get("items", []):
        meta[str(it["field_name"])] = int(it.get("type", FT_TEXT))
    _fields_cache["meta"], _fields_cache["ts"] = meta, time.time()
    return meta


# ============================================================
# 值转换
# ============================================================
def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _date_ms(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        if f > 1e11:     # 已经是毫秒时间戳
            return int(f)
    try:
        d = pd.to_datetime(str(v)).date()
    except Exception:
        return None
    return int(datetime(d.year, d.month, d.day, tzinfo=CN_TZ).timestamp() * 1000)


def to_cell(value, ftype: int):
    if ftype == FT_NUMBER:
        return _num(value)
    if ftype == FT_DATE:
        return _date_ms(value)
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def from_cell(value, ftype: int):
    if value is None:
        return None
    if ftype == FT_DATE:
        try:
            ts = float(value) / 1000
            return datetime.fromtimestamp(ts, CN_TZ).strftime("%Y-%m-%d")
        except Exception:
            return str(value)
    if ftype == FT_NUMBER:
        return _num(value)
    # 多行文本可能返回 [{"text": "...", "type": "text"}]
    if isinstance(value, list):
        parts = []
        for p in value:
            if isinstance(p, dict):
                parts.append(str(p.get("text", "")))
            else:
                parts.append(str(p))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text", value))
    return value


# ============================================================
# 读
# ============================================================
def list_records() -> list[dict]:
    """返回 [{record_id, fields}]"""
    c = _cfg()
    url = f"{BASE_URL}/bitable/v1/apps/{c['app_token']}/tables/{c['table_id']}/records"
    out, page_token = [], None

    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, headers=_headers(), params=params, timeout=20)
        data = _check(r.json(), "读取记录")
        out.extend(data.get("items", []))
        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break
    return out


def read_dataframe(columns: list[str]) -> pd.DataFrame:
    meta = fields_meta()
    rows = []
    for rec in list_records():
        f = rec.get("fields", {}) or {}
        row = {}
        for col in columns:
            row[col] = from_cell(f.get(col), meta.get(col, FT_TEXT))
        row["_record_id"] = rec.get("record_id")
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=columns)
    return df.drop(columns=["_record_id"], errors="ignore")


# ============================================================
# 写（按「日期」唯一，存在则更新，不存在则新增）
# ============================================================
def upsert(row: dict, key_field: str = "日期", columns: list[str] | None = None) -> str:
    meta = fields_meta()
    c = _cfg()
    url_rec = f"{BASE_URL}/bitable/v1/apps/{c['app_token']}/tables/{c['table_id']}/records"

    key_val = str(row.get(key_field, "")).strip()

    # 找同一天的旧记录
    target_id = None
    for rec in list_records():
        f = rec.get("fields", {}) or {}
        if str(from_cell(f.get(key_field), meta.get(key_field, FT_TEXT))).strip() == key_val:
            target_id = rec.get("record_id")
            break

    fields_payload = {}
    for k, v in row.items():
        if k in ("_record_id",):
            continue
        fields_payload[k] = to_cell(v, meta.get(k, FT_TEXT))

    body = {"fields": fields_payload}
    if target_id:
        r = requests.put(f"{url_rec}/{target_id}", headers=_headers(), json=body, timeout=20)
        _check(r.json(), "更新记录")
        return target_id

    r = requests.post(url_rec, headers=_headers(), json=body, timeout=20)
    data = _check(r.json(), "新增记录")
    rec = data.get("record", {}) or {}
    return rec.get("record_id", "")


def ensure_fields(columns: list[str]) -> None:
    """表里缺列时自动补（全部按文本建，安全）"""
    meta = fields_meta(refresh=True)
    missing = [c for c in columns if c not in meta]
    if not missing:
        return

    c = _cfg()
    url = f"{BASE_URL}/bitable/v1/apps/{c['app_token']}/tables/{c['table_id']}/fields"
    for name in missing:
        requests.post(url, headers=_headers(),
                      json={"field_name": name, "type": FT_TEXT}, timeout=20)
    fields_meta(refresh=True)


def self_check() -> str:
    """连通性自检，返回一句人话结果"""
    if not configured():
        return "未配置飞书凭证（FEISHU_APP_ID / APP_SECRET / APP_TOKEN / TABLE_ID）"
    try:
        _tenant_token(force=True)
        meta = fields_meta(refresh=True)
        return f"连通正常，表格共 {len(meta)} 个字段"
    except Exception as e:
        return f"连接失败：{type(e).__name__} {e}"
