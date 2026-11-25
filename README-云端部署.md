# 云端部署说明（Streamlit Cloud + 飞书多维表格）

目标：**不用开电脑，手机打开网址就能看**；每天的数据自动记到飞书多维表格里，随时翻历史。

整套东西分三块，各自独立、互不拖累：

| 块 | 干什么 | 跑在哪 |
|---|---|---|
| 网页看板 | 打开就抓实时数据、算指标、显示彩色结论 | Streamlit Cloud（免费，公网可访问） |
| 历史记录 | 每天一条快照，存飞书多维表格 | 飞书（手机 App 也能直接翻） |
| 每日抓取 | 每天收盘后自动跑一次写飞书 | GitHub Actions（免费，定时） |

---

## 一、飞书这边（约 10 分钟）

### 1. 建表格：直接导入现成的 CSV

1. 打开飞书 →「云文档」→ 新建 → **多维表格** → 选「导入表格」。
2. 选本地这个文件：`历史记录\市场阶段历史.csv`（里面已有 3 条真实记录，导入后就是现成的表头和格式）。
3. 表格建好后，把浏览器地址栏里的链接复制下来，形如：
   `https://xxx.feishu.cn/base/`**`XXXXXXXX`**`?table=`**`tblYYYYYYYY`**
   - `/base/` 后面那串（通常是 `bcn...` 或一长串字符）= **app_token**
   - `table=` 后面那串（`tbl...`）= **table_id**

> 也可以建成空表，程序会自动补齐缺的列（全部按文本建）。

### 2. 建自建应用（拿钥匙）

1. 浏览器打开 <https://open.feishu.cn/app> → 「创建企业自建应用」，名字随便，比如 `A股看板`。
2. 进应用 → 左侧「权限管理」→ 添加权限：
   - `bitable:app`（查看、评论、编辑和管理多维表格）——写入必须有这个
3. 左侧「凭证与基础信息」→ 抄下 **App ID**（`cli_...`）和 **App Secret**。
4. 左侧「版本管理与发布」→ 创建版本 → 申请发布（企业自建应用一般要管理员点一下通过）。
5. **关键一步**：回到那张多维表格 → 右上角「···」→「添加文档应用」→ 把刚建的应用加进去（否则应用没有读写权限，会报错 91403 / 1254302 之类）。

### 3. 本地自检（先确认钥匙能用）

在项目目录执行：

```powershell
python feishu_check.py
```

返回「连通正常，表格共 N 个字段」就说明打通了。想顺手写一条测试记录：

```powershell
python feishu_check.py --write     # 会写一条 2099-01-01 的测试行，验完在表里删掉即可
```

---

## 二、GitHub 这边（约 5 分钟）

1. 新建一个**私有**仓库（Private），名字随意，比如 `a-share-dashboard`。
   > 私有仓库免费额度足够；用 Public 也行，但记得不要把密钥提交上去。
2. 把项目目录里这些文件传上去（网页「Add file → Upload files」直接拖也行）：

```
app.py
market_core.py
storage_feishu.py
daily_snapshot.py
requirements.txt
runtime.txt
feishu_check.py
.streamlit/config.toml
.github/workflows/daily.yml
```

**不要上传**：`历史记录/`、`日志/`、`.streamlit/secrets.toml`、`启动分析页面.bat`（`.gitignore` 已经帮你挡了）。

3. 仓库 → Settings → Secrets and variables → **Actions** → 新建 4 个 secret：

| Name | Value |
|---|---|
| `FEISHU_APP_ID` | `cli_...` |
| `FEISHU_APP_SECRET` | 应用密钥 |
| `FEISHU_APP_TOKEN` | 表格链接里 `/base/` 后面那串 |
| `FEISHU_TABLE_ID` | `tbl...` |

4. 仓库 → Actions → 左边选「每日A股快照」→ 右侧 **Run workflow** 手动跑一次，看日志。
   成功的话飞书表里会多出今天这条；失败看日志最后几行，多半是权限或表格 ID 问题。

---

## 三、Streamlit Cloud 部署（约 5 分钟）

1. 打开 <https://share.streamlit.io>，用 GitHub 账号登录（首次要授权读仓库）。
2. 「New app」→ 选刚才的仓库 → Branch `main` → Main file path 填 **`app.py`** → Deploy。
3. 起了之后点右下「Settings」→ **Secrets**，粘贴：

```toml
HISTORY_BACKEND = "feishu"

APP_PASSWORD = "你自己的访问口令"

FEISHU_APP_ID = "cli_xxx"
FEISHU_APP_SECRET = "xxx"
FEISHU_APP_TOKEN = "xxx"
FEISHU_TABLE_ID = "tblxxx"
```

保存后会自动重启。之后打开网址先要输口令，输一次当次会话就记住。

4. 拿到的链接形如 `https://xxx.streamlit.app`，**在手机浏览器里收藏**（加到主屏幕，用起来跟 App 差不多）。

---

## 四、三个必然踩到的坑

1. **免费实例会休眠**：约 12 小时没人访问就睡，第一次打开要等 30–60 秒唤醒，属正常。
2. **海外 IP 抓 A 股数据**：Streamlit Cloud 和 GitHub Actions 的服务器都在美国，理论上抓新浪/上交所接口可能被拒或超时。部署完第一件事就是在网页上确认「板块一二三」的数字不是 0/—；Actions 也要看第一次运行日志。若确实抓不到，退路是把「抓取」这一步换回国内网络执行（网页只负责展示），这个方案需要时再说。
3. **每月免费额度**：Streamlit Cloud 免费版有社区额度，个人用这个看板（每天点几次）完全够；Actions 免费 2000 分钟/月，本任务每次 1–3 分钟，一个月也就 60 分钟上下。

---

## 五、日常怎么用

- 手机点开链接 → 看结论（红=利好、绿=利空）→ 想翻历史就打开飞书那张多维表格，两边数据是同一份。
- 想改结论口径：只改 `market_core.py` 里的阈值，网页和 Actions 一起生效（等 Actions 下一次跑）。
- 网页上的「下载 CSV / Excel」仍然可用，是从飞书拉的当前数据现场生成，手机上也能存下来。
- 手动补一天：仓库 Actions 页面点「Run workflow」；或在本机 PowerShell 里 `$env:HISTORY_BACKEND="feishu"` 后跑 `python daily_snapshot.py`。
