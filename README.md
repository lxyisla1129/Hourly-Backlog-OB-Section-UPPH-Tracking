# Hourly Picking & Packing UPPH Tracking

这是独立工具，代码、Chrome 用户资料、刷新状态和导出数据都保存在本工具目录。可以与另一个工具同时运行，需要分别初始化 SSO。

## Windows 安装与启动

在本目录打开 PowerShell，首次执行

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py --login --share-lan
```

后续运行 `.\.venv\Scripts\python.exe start.py --share-lan`。
本机访问 http://localhost:8501 ，同事访问 http://公司电脑内网IP:8501 。防火墙与网络需要允许访问。当前页面没有内置登录，需要公司受控网络或访问代理。

## Mac 安装与启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python start.py --login --share-lan
```

## 运行规则

接电、保持电脑唤醒和终端运行。Ctrl+C 停止本工具。每两小时串行刷新四仓，手动刷新只采集所选仓，全局冷却 30 分钟。会话过期需重新启动并加 --login。网页按本工具的数据目录读取。

仅采集 Picking 和 Packing，包含每日、小时、员工与 WTD UPPH。客户使用 Owner Name，LAX2/LAX5 的 2B 分组沿用现有规则。UPPH 采用活动跨度小时。
原始日报与汇总快照保留七天。首次登录会在本工具 .runtime 中生成会话，现有另一工具的会话不会随安装包分发。

## GitHub 与更新

上传本目录内代码到 Private 仓库。排除 .runtime、iwms-exports、.venv、凭据和日志。更新时保留上述本地运行目录。停止旧程序后覆盖代码并重新安装 requirements.txt，再启动。

黑五工具停止、删除或更新时，本工具仍可独立运行。

代码已经过本地检查。Windows 浏览器全流程需要在目标电脑验收。
