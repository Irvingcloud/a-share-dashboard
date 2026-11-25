# -*- coding: utf-8 -*-
"""
飞书多维表格连通性自检。

用法（在项目目录）：
    python feishu_check.py            # 只检查
    python feishu_check.py --write    # 额外写一条测试记录（日期 2099-01-01，验完可删）

凭证来源：环境变量或 .streamlit/secrets.toml，二选一。
"""

import os
import sys
from pathlib import Path

# 本地跑的时候把 secrets.toml 读进来当环境变量用（线上用环境变量）
try:
    import tomllib
    sec = Path(".streamlit/secrets.toml")
    if sec.exists():
        data = tomllib.loads(sec.read_text(encoding="utf-8"))
        for k, v in data.items():
            os.environ.setdefault(k, str(v))
        print(f"已读取 {sec}")
except Exception as e:
    print(f"读取 secrets.toml 跳过：{type(e).__name__}")

import market_core as mc
import storage_feishu as sf

print("=" * 50)
print("后端:", mc.HISTORY_BACKEND)
print("凭证齐全:", sf.configured())
print("自检:", sf.self_check())

if sf.configured():
    try:
        df = sf.read_dataframe(mc.SNAPSHOT_COLUMNS)
        print(f"历史记录 {len(df)} 条")
        if not df.empty:
            print("最新一条：")
            print(df.tail(1).T.to_string())
    except Exception as e:
        print(f"读取失败：{type(e).__name__} {e}")

if "--write" in sys.argv and sf.configured():
    test_row = {c: None for c in mc.SNAPSHOT_COLUMNS}
    test_row["日期"] = "2099-01-01"
    test_row["盘面简述"] = "连通性测试，可删除"
    test_row["市场宽广度(%)"] = 66.6
    rid = sf.upsert(test_row, key_field="日期", columns=mc.SNAPSHOT_COLUMNS)
    print(f"测试记录已写入，record_id={rid}（在表格里删掉 2099-01-01 那行即可）")
