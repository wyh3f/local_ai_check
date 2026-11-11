# 本地 AI 部署能力检测 (local_ai_check)

在本机运行的开源大模型部署能力检测工具。自动读取 CPU、内存、显卡、磁盘信息，评估可部署的模型等级，不联网、不上传任何数据。

## 功能

- 自动采集系统 / CPU / 内存 / 显卡 / 磁盘详情
- 6 档模型等级评估：入门级 (≤1.5B) → 旗舰级 (70B+)
- 速度预估（带宽模型，区分不同显卡/内存配置的实际性能）
- 界面文本可鼠标选中复制
- 报告导出：txt 文本 或 PNG 长图（滚动拼接）
- 命令行模式：`cs_ai_check.exe --cli`
- 完全离线，所有检测在本机完成

## 下载

前往 [GitHub Releases](https://github.com/wyh3f/local_ai_check/releases) 下载最新版本 exe。

## 系统要求

Windows 10 / 11 64 位，单文件免安装。

## 源码运行

```powershell
conda create -n exe_ai_check python=3.11 -y
conda activate exe_ai_check
pip install customtkinter>=6.0
python src/app.py
```

## 打包

```powershell
pyinstaller --noconfirm --clean cs_ai_check.spec
# 产物在 dist/cs_ai_check.exe
```

## 版本历史

| Tag | 说明 |
| --- | --- |
| v1.3 | 文本可选中、速度带宽模型、PNG 长图导出、关于对话框重写 |
| v1.2 | customtkinter 界面重构、模块拆分、智能加载态、轻量动效 |
| v1.1 | DPI 感知、保存报告、补充硬件字段（CPU 占用率/驱动/磁盘型号） |
| v1.0 | 功能基线：ttk 界面、6 档等级评估、硬件采集 |

使用 `git log` 查看完整提交历史，或在 GitHub 上切换 tag 查看任意版本源码。

## 项目结构

```
src/
  app.py            # 主程序入口 + customtkinter 界面
  evaluator.py      # 等级评估 + 速度带宽模型
  hardware.py       # 硬件信息采集
  report.py         # 纯文本报告生成
  selftest.py       # 可移植性自检
  platform_win.py   # 资源定位 / DPI / PNG 截图编码
  appinfo.py        # 程序名与版本号
  assets/           # 图标资源
cs_ai_check.spec    # PyInstaller 打包配置
cs_ai_check.manifest
version_info.txt    # exe 版本资源
requirements.txt
portability/        # Windows 沙箱自检脚本
```
