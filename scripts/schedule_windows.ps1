# Registers (or updates) a daily Windows Task Scheduler job that runs the bot.
# Usage (from the project folder, normal PowerShell, no admin needed):
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1            # daily at 09:00
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 21:30
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove
param(
    [string]$Time = "09:00",
    [string]$TaskName = "InternshipAssistant",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python. Follow the setup steps in README.md first."
}

$action = New-ScheduledTaskAction -Execute $python -Argument "run.py --trigger schedule" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: if the PC was off at the scheduled time, run as soon as it's back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Fetches and filters internships daily. Never submits applications." -Force | Out-Null

Write-Host "Scheduled '$TaskName' daily at $Time (runs as you, only when you're logged in)."
Write-Host "Run it now to test:  Start-ScheduledTask -TaskName $TaskName"
