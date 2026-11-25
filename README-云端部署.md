# A股市场阶段分析 · 云端版

线上地址（手机、iPad、任何电脑都能打开，不用开电脑）：
**https://a-share-dashboard.streamlit.app**

代码仓库：https://github.com/Irvingcloud/a-share-dashboard

---

## 现在是怎么跑的

| 环节 | 谁在做 | 说明 |
| --- | --- | --- |
| 打开网页看数据 | Streamlit Community Cloud（免费） | 每次打开实时抓一次（约 30–60 秒），空闲约 12 小时会休眠，首次打开需唤醒 30–60 秒 |
| 每天自动抓一条记录 | GitHub Actions | 工作日北京时间 16:10 自动跑，结果写进仓库根的 `history.csv` |
| 历史记录 | 仓库里的 `history.csv` | 每次写入就是一次 commit，手机在 GitHub 上也能直接看；网页里的「下载 CSV / Excel」也能带走 |
| 手动补一条 | 网页左侧「自动记录今日快照」开关 | 打开后页面抓完数会自动写一条（同一天会覆盖，不会重复） |

> 飞书多维表格这条路走不通：豆包账号这个飞书租户的平台策略禁止自建应用调用开放接口（取 token 直接返回 21001），应用、权限、发布、协作者都配好了也没用，所以改用仓库 CSV。

## 日常怎么用

- 想看当天盘面：直接开网址，等进度条走完即可。
- 想补记今天：打开左侧「自动记录今日快照」，再点「刷新数据」。
- 想立刻跑一次定时任务（比如收盘后忘了）：仓库 → Actions → 每日A股快照 → Run workflow。
- 想下载历史：页面底部「下载 CSV / 下载 Excel」。

## 需要留意的几件事

1. **GitHub Token 有有效期**：当前 Streamlit 里配置的 token 到期日是 2026-12-25。
   到期前重新生成一个（github.com/settings/tokens，勾 `repo` + `workflow`），
   然后到 share.streamlit.io → 应用 → Settings → Secrets 里替换 `GITHUB_TOKEN` 即可，
   代码不用改。
2. **定时任务只有工作日**：cron 是 `10 8 * * 1-5`（UTC）= 北京时间 16:10，周末和节假日不跑；
   GitHub 的定时任务偶尔会延迟几分钟到半小时。
3. **数据源是新浪**：期指、全市场快照、ETF 行情、外盘期货都走新浪；
   期指已改成逐合约直连（`hq.sinajs.cn`），因为 akshare 那个接口会混进已到期的老合约，
   贴水率会算成几十个点。若某天页面提示「数据未获取到」，多半是该接口临时抽风，
   点「刷新数据」重试即可。
4. **ETF 份额来自上交所**：正常 T 日盘后才有 T 日数据，所以页面里的「ETF 份额日期」
   一般比当天晚一天，属正常。
5. **页面是公开的**：仓库和站点都是公开的，任何拿到链接的人都能看。
   里面只有行情数据和你的记录，没有密钥；如想加访问口令，在 Secrets 里加
   `APP_PASSWORD = "你的口令"` 就会生效。

## 本地还留着什么

本机这套（`启动分析页面.bat` + 8501 端口 + 计划任务）仍然可用，逻辑和云端完全一致，
只是历史默认写本地 `历史记录\市场阶段历史.csv / .xlsx`。
本地想改成写仓库 CSV，把 `.streamlit\secrets.toml` 里 `HISTORY_BACKEND` 设成 `github` 即可。

## 排查清单

- 网页一直转圈：等 60 秒；仍不行就到 share.streamlit.io → 应用右侧 ⋮ → Reboot。
- 网页报 `GITHUB_TOKEN`/403：token 过期或权限不足，按上面第 1 条换 token。
- Actions 失败：仓库 → Actions → 点进失败的那次 → 看「抓取并写入」步骤日志，
  常见是新浪接口超时，重跑即可。
- 想换抓取时间：改 `.github\workflows\daily.yml` 里的 cron（UTC 时间，北京时间 = UTC+8）。
