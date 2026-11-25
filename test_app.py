# -*- coding: utf-8 -*-
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app.py", default_timeout=300)
at.run()
print("异常:", len(at.exception), [str(e.value) for e in at.exception])
print("\n===== 指标 =====")
for m in at.metric:
    print(" -", m.label, "=", m.value, "|", m.delta)
print("\n===== 提示 =====")
for s in at.success:
    print("SUCCESS:", s.value)
for i in at.info:
    print("INFO:", i.value)
for w in at.warning:
    print("WARN:", w.value)
print("\n===== 结论 =====")
for m in at.markdown:
    if "中期" in m.value and "concl" in m.value:
        print(m.value.replace("<br><br>", "\n").replace("<br>", "\n"))
print("\n===== 表格 =====")
for d in at.dataframe:
    print(d.value.shape, "|", list(d.value.columns)[:6])
    print(d.value.head(9).to_string())
