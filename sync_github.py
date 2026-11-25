# -*- coding: utf-8 -*-
"""把本地文件同步到 GitHub 仓库（走 Contents API，绕开 git 直连不稳的问题）。

用法：python sync_github.py [文件名...]
"""

import base64
import os
import sys
from pathlib import Path

import requests

try:
    import tomllib
    sec = Path(".streamlit/secrets.toml")
    if sec.exists():
        for k, v in tomllib.loads(sec.read_text(encoding="utf-8")).items():
            os.environ.setdefault(k, str(v))
except Exception:
    pass

TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ.get("GITHUB_REPO", "Irvingcloud/a-share-dashboard")
API = f"https://api.github.com/repos/{REPO}/contents"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

DEFAULT_FILES = [
    "app.py", "market_core.py", "signal_panel.py", "signal_ref.csv",
    "build_signal_ref.py", "storage_github.py", "storage_feishu.py",
    "daily_snapshot.py", "requirements.txt", "runtime.txt", "feishu_check.py",
    "test_app.py", "README.md", "README-云端部署.md", ".gitignore",
    ".github/workflows/daily.yml", ".streamlit/config.toml",
    ".streamlit/secrets.toml.example",
]


def push_file(rel: str, message: str) -> str:
    p = Path(rel)
    if not p.exists():
        return f"跳过（本地没有）：{rel}"
    content = base64.b64encode(p.read_bytes()).decode("ascii")
    url = f"{API}/{requests.utils.quote(rel)}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r2 = requests.put(url, headers=HEADERS, json=payload, timeout=30)
    if r2.status_code not in (200, 201):
        return f"失败 {rel}: {r2.status_code} {r2.text[:150]}"
    return f"已更新 {rel}"


if __name__ == "__main__":
    files = sys.argv[1:] or DEFAULT_FILES
    for f in files:
        print(push_file(f, "同步云端版：" + Path(f).name))
