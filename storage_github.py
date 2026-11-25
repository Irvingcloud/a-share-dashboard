# -*- coding: utf-8 -*-
"""
把历史记录存到 GitHub 仓库里的一个 CSV 文件（云端持久化，不依赖电脑开机）。

需要环境变量 / Streamlit Secrets：
    GITHUB_TOKEN   GitHub Personal Access Token（repo 权限）
    GITHUB_REPO    owner/repo，例如 Irvingcloud/a-share-dashboard
    GITHUB_HIST_PATH  可选，CSV 在仓库里的路径，默认 history.csv

读写都走 GitHub Contents API，每次写入就是一次 commit，天然有版本记录。
"""

import base64
import io
import os
from typing import Optional

import pandas as pd
import requests

API = "https://api.github.com"
TIMEOUT = 20


class GitHubError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def token() -> str:
    return _env("GITHUB_TOKEN")


def repo() -> str:
    return _env("GITHUB_REPO")


def path() -> str:
    return _env("GITHUB_HIST_PATH", "history.csv")


def configured() -> bool:
    return bool(token() and repo())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url() -> str:
    return f"{API}/repos/{repo()}/contents/{path()}"


def _fetch_file() -> tuple[Optional[str], Optional[str]]:
    """返回 (解码后的文本, sha)；文件不存在返回 (None, None)"""
    r = requests.get(_url(), headers=_headers(), timeout=TIMEOUT)
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise GitHubError(f"读取失败 {r.status_code}: {r.text[:200]}")
    data = r.json()
    if isinstance(data, list):
        raise GitHubError("这个路径是目录，不是 CSV 文件")
    raw = base64.b64decode(data.get("content", ""))
    return raw.decode("utf-8-sig"), data.get("sha")


def read_dataframe(columns: list[str] | None = None) -> pd.DataFrame:
    text, _ = _fetch_file()
    if not text or not text.strip():
        return pd.DataFrame(columns=columns or [])
    df = pd.read_csv(io.StringIO(text), encoding="utf-8-sig", dtype=str)
    if columns:
        for c in columns:
            if c not in df.columns:
                df[c] = None
    return df


def write_dataframe(df: pd.DataFrame, message: str = "更新历史记录") -> None:
    sha = None
    try:
        _, sha = _fetch_file()
    except GitHubError:
        sha = None

    text = df.to_csv(index=False, encoding="utf-8-sig")
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(_url(), headers=_headers(), json=payload, timeout=TIMEOUT)
    if r.status_code not in (200, 201):
        raise GitHubError(f"写入失败 {r.status_code}: {r.text[:200]}")


def upsert(row: dict, key_field: str = "日期", columns: list[str] | None = None) -> None:
    """按 key_field 更新或追加一行，然后整体提交"""
    df = read_dataframe(columns)
    key = str(row.get(key_field))
    if not df.empty and key_field in df.columns:
        df = df[df[key_field].astype(str) != key]
    df = pd.concat([df, pd.DataFrame([{k: ("" if v is None else v) for k, v in row.items()}])],
                   ignore_index=True)
    if columns:
        keep = [c for c in columns if c in df.columns] + \
               [c for c in df.columns if c not in columns]
        df = df.reindex(columns=keep)
    if key_field in df.columns:
        df = df.sort_values(key_field)
    write_dataframe(df, message=f"更新 {key} 的盘面记录")


def self_check() -> str:
    if not configured():
        return "未配置 GITHUB_TOKEN / GITHUB_REPO"
    try:
        text, sha = _fetch_file()
        n = 0 if not text else max(len(text.strip().splitlines()) - 1, 0)
        return f"连接正常，仓库 {repo()}，文件 {path()}，现有 {n} 行" + ("（文件不存在，首次写入时创建）" if sha is None else "")
    except Exception as e:
        return f"连接失败：{type(e).__name__} {e}"
