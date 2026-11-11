# Windows Sandbox 冒烟测试：自检 + 窗口启动探针
# 由 smoke_test.wsb 的 LogonCommand 自动执行，结果写入 C:\test（映射回宿主的 portability 目录）

$ErrorActionPreference = "Continue"
$root = "C:\test"
$exe = "C:\dist\cs_ai_check.exe"
$log = Join-Path $root "smoke_log.txt"
$json = Join-Path $root "selftest.json"

"=== 便携性冒烟测试 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding utf8
"系统：$([System.Environment]::OSVersion.VersionString)" | Out-File $log -Append -Encoding utf8
"架构：$env:PROCESSOR_ARCHITECTURE" | Out-File $log -Append -Encoding utf8
"用户：$env:USERNAME" | Out-File $log -Append -Encoding utf8
"exe 存在：$(Test-Path $exe)" | Out-File $log -Append -Encoding utf8

# 1. 自检模式：检查单文件环境、Tk 栈、硬件采集、报告生成
$proc = Start-Process -FilePath $exe -ArgumentList "--selftest", $json -Wait -PassThru
"自检退出码：$($proc.ExitCode)（0=通过，1=有失败项，2=结果写不出）" | Out-File $log -Append -Encoding utf8

# 2. 窗口探针：真实启动 GUI，确认窗口出现
$gui = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 10
$win = Get-Process cs_ai_check -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($win) {
    "窗口：已出现，标题「$($win.MainWindowTitle)」" | Out-File $log -Append -Encoding utf8
} else {
    "窗口：未出现（启动失败或无桌面会话）" | Out-File $log -Append -Encoding utf8
}
Get-Process cs_ai_check -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
"=== 测试结束 ===" | Out-File $log -Append -Encoding utf8
