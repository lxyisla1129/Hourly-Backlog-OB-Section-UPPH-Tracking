# Hourly Backlog & OB Section UPPH Tracking

在一台能访问 iWMS 的公司电脑上运行。Chrome 负责正常网页导出，Streamlit 显示四仓数据。仓库为 LAX1、LAX2、LAX4、LAX5。

## 首次安装

安装 Python 3.11 或更新版本，以及 Google Chrome。解压或克隆本仓库，在此目录打开终端。

Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python start.py --login
```

Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py --login
```

在打开的 Chrome 完成公司 SSO 和 MFA，进入英文 iWMS 页面后按终端提示继续。登录资料保存在本机 .runtime 目录。公司电脑首次登录需要初始化，不会携带开发者的登录状态。

## 日常启动

Mac 激活 .venv 后运行 `python start.py`。Windows 运行 `.\.venv\Scripts\python.exe start.py`。

打开 http://localhost:8501。终端保持运行，电脑接电并保持唤醒。Ctrl+C 停止服务。SSO 过期后停止服务，重新运行带 --login 的命令完成验证。

## 同一公司网络共享

运行 `python start.py --share-lan`。同事访问 http://公司电脑的内网IP:8501。网络隔离和防火墙可能需要 IT 配置。当前页面没有内置用户登录，需要用公司网络或受控代理限制访问，查看者会看到导出的仓库和员工数据。不要把 8501 端口暴露到公网。

## 数据与计算

每两小时串行采集四仓 Picking、Packing 和综合订单报表。同一仓同一天的文件覆盖更新，保留今天和前六天的文件。页面手动刷新全局限制为每 30 分钟一轮，需要采集进程在线。

Picking 按实际拣货件数和活动跨度计算。Packing 使用复核件数、复核时间。UPH 为活动跨度估计，同一员工跨业务组的时间可重叠，各组小时数不适合直接相加。客户使用 Owner Name。

LAX2 货主名包含 Belk、Walmart、Kohl's、Kohl’s 或 JCP 的记录单列 Trafilea 2B。LAX5 精确匹配 Kreassive, inc. 的记录单列 Kreassive 2B。普通业务排除对应记录。

积压统计按洛杉矶时间，前一天 15:00 至当天 15:00 前收单，打包截止当天 17:30，排除 SPB Name 包含 2B 或 selfpickup 的订单。按 JD Order NO. 计数，所有明细行有 Packed Time 才算完成。取消单目前计入。

大报表进入任务列表时，等待本次任务完成后下载。任务创建时间按洛杉矶时间匹配，服务端时间差或页面变化可能使本轮失败。失败时旧文件保留。空报告目前会提示失败。

## 发布到个人 GitHub

先确认公司允许将这套业务代码存放在个人 GitHub。新建 Private 仓库，将这个目录里的代码、requirements.txt、README.md 和 .gitignore 上传到仓库根目录。可以使用 GitHub 网页 Upload files，上传前检查文件列表。

只上传本代码包。导出的 Excel、.env、Chrome 用户资料、Cookie、日志、.runtime、.venv 都排除。文件名以点开头的 .gitignore 也要包含，Git 命令上传时会遵守其中规则，网页手动上传仍需自己检查所选文件。

GitHub 用于分发版本。此版本由公司电脑采集并提供页面。部署到 Streamlit Community Cloud 还需要共享存储和远程刷新通道。

## 验收

1. 首次 SSO 后，四仓应各出现 Picking、Packing、backlog 三个日期文件。
2. 页面切换仓库和日期，检查三类数据与网页导出一致。
3. 检查 LAX2/LAX5 普通与 2B 分组。
4. 保持运行两小时，确认文件修改时间更新。
5. 验证手动刷新请求、冷却限制及过期会话提示。
6. 同事通过公司内网地址访问并确认权限范围。

原环境为 macOS。打包版本的 Windows 文件锁已改为 portalocker，Windows 全流程仍需在目标电脑验收。

## Updated dashboard rules

Backlog Summary includes independent pending picking, recheck, packing and handover counts. A missing timestamp on any detail line means that order is pending that stage. These counts can overlap. BL is a reference based on a 24h baseline. Friday 15:00 through Monday 15:00 receipts are due Monday 17:30. Saturday and Sunday views show the Monday cohort available in that export. Export receipt filters use the same window.

WTD covers Monday through the selected date, honors the selected business, customer and employee filters, and divides total units by the sum of daily activity hours. Missing daily exports are shown explicitly. Older missing days require backfilling. Seven-day retention still applies.

## Weekly backlog

The integrated export covers the union of the selected date and preceding six calendar-date receipt cohorts, retaining weekend rollover. Weekly totals use unique orders, exclude SPB 2B/selfpickup, and use Packed Time for completion. Total backlog excludes orders completed since an earlier daily cohort. This is the state at the latest collection time.

Backlog is collected once per date; UPPH continues every two hours. Use --force-backlog for another same-day backlog collection. Each backlog Excel has a coverage JSON companion, removed with expired data. Previous daily-only exports cannot supply weekly totals.

LAX5 test after stopping the running collector

```bash
python iwms_fixed.py 'https://pulsar-login.jd.com/login.html?ReturnUrl=https%3A%2F%2Fiwms-us.jd.com%2F#/' --warehouse LAX5 --profile-warehouse LAX1 --workflows backlog --force-backlog --click-login --strict
```
