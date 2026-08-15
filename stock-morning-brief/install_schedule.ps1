# install_schedule.ps1 — 把晨報註冊成 Windows 工作排程器任務（每天自動跑）
#
# 用法（一般使用者權限即可，不需系統管理員）：
#   powershell -ExecutionPolicy Bypass -File install_schedule.ps1
#   powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -At "08:07"
#
# 移除：Unregister-ScheduledTask -TaskName "股票晨報" -Confirm:$false
#
# macOS / Linux 請改用 cron（README 有範例）。
#
# 為什麼用系統排程器而不是 AI 工具內建的排程：
#   AI 工具的排程通常每次觸發都開一個新對話 session（對話列表會被灌爆），
#   遇到未授權的指令還會跳權限詢問等人按「允許」，沒人按就整份卡死。
#   直接讓系統排程器跑 brief.py 則全程無對話、無權限詢問、無通知。

param(
    [string]$At = "",                    # 時間 HH:mm；留空則讀 config.yaml 的 notify_time
    [string]$TaskName = "股票晨報"
)

$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$Script = Join-Path $ProjectDir "brief.py"
if (-not (Test-Path $Script)) { throw "找不到 brief.py：$Script" }

# ── 找 pythonw.exe（完全不跳黑色命令視窗；輸出改寫進 last_run.log）──
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "PATH 上找不到 python，請先安裝 Python 3.10+ 並加入 PATH" }
$Pythonw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    Write-Warning "找不到 pythonw.exe，改用 python.exe（每天執行時會閃一下命令視窗）"
    $Pythonw = $py
}

# ── 決定執行時間 ──
if (-not $At) {
    $cfg = Join-Path $ProjectDir "config.yaml"
    if (Test-Path $cfg) {
        $m = [regex]::Match((Get-Content $cfg -Raw -Encoding UTF8), 'notify_time:\s*"?(\d{1,2}:\d{2})"?')
        if ($m.Success) { $At = $m.Groups[1].Value }
    }
}
if (-not $At) { $At = "08:07" }
# 小提醒：整點（:00）是所有人排程的尖峰，錯開幾分鐘比較不會塞車

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Script`"" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# InteractiveToken：以登入中的你執行，不需存密碼；缺點是電腦要處於登入狀態
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "股票晨報：每天 $At 產出簡報＋K線圖並推 Telegram。無人值守，不開對話、不問權限。主腳本 $Script" `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
"OK 已註冊排程「$TaskName」"
"   執行時間：每天 $At"
"   下次執行：$($info.NextRunTime)"
"   除錯看：$(Join-Path $ProjectDir 'last_run.log')"
