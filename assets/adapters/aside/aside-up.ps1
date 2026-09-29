# agentic-vault:adapter aside-up engine=0.17.0
<#
  aside-up.ps1 - start the Aside app for the aside CLI without taking the
  user's window focus. Windows only. ASCII only so Windows PowerShell 5.1
  reads it correctly without a byte order mark.

  Contract:
    - Idempotent. If Aside and its daemon are already running, no window is
      touched (the user may be working in it).
    - When it starts Aside, the new browser window is minimized as soon as it
      appears and the window that was in the foreground before the launch gets
      the focus back.
    - One output line, prefixed "[aside]".
    - Exit 0 = CLI ready (or already running), 1 = not ready. With -Hook the
      exit code is always 0 so a PreToolUse hook never blocks the command,
      and the hook input is checked so only commands that run aside act.

  Measured on Windows 11 (Aside app 1.0.928.2, CLI 1.26.916.1741, 2026-09-29):
    1. The aside CLI talks to aside-daemon.exe. The browser starts the daemon
       and force-kills it when the browser exits, so closing the last Aside
       window stops the CLI ("Aside isn't running").
    2. --no-startup-window makes the browser exit at once; the daemon never
       starts. A window has to exist.
    3. start /min (SW_SHOWMINNOACTIVE) is ignored; the window opens normally
       and takes the focus for about two seconds.
    4. With the window minimized, the youtube skill, openTab, evaluate,
       screenshot and closeTab all work and the window stays minimized.
  Record the result of your own run in the vault's Aside operations guide
  before relying on these facts for another app version.
#>
param(
    [int]$TimeoutSec = 30,
    [string]$AsideExe = '',
    [switch]$Hook
)
$ErrorActionPreference = 'SilentlyContinue'

function Out-One([string]$s) { Write-Output "[aside] $s" }
function Stop-With([int]$code) { if ($Hook) { exit 0 } else { exit $code } }

function Find-AsideExe {
    # An explicit path (parameter, then ASIDE_EXE) is used alone: when it is
    # wrong the script reports it instead of starting some other install.
    $explicit = if ($AsideExe) { $AsideExe } elseif ($env:ASIDE_EXE) { $env:ASIDE_EXE } else { '' }
    if ($explicit) {
        if (Test-Path -LiteralPath $explicit -PathType Leaf) { return $explicit }
        return $null
    }
    $candidates = @()
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Aside\Application\Aside.exe') }
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'Aside\Application\Aside.exe') }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Aside\Application\Aside.exe') }
    foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { return $c } }
    return $null
}

function Test-Running {
    return [bool]((Get-Process -Name Aside) -and (Get-Process -Name aside-daemon))
}

function Test-Cli {
    if (-not (Get-Command aside)) { return $false }
    $o = & aside repl "console.log('aside-ready')" 2>&1 | Out-String
    return ($o -match 'aside-ready')
}

if ($env:OS -ne 'Windows_NT') { Out-One 'Windows only - skipped'; Stop-With 1 }

# Defense in depth for the hook: the settings use an `if` filter, but a host
# that ignores it would call this before every shell command. Read the hook
# input and stop at once unless the command runs aside.
if ($Hook) {
    $raw = ''
    try { if ([Console]::IsInputRedirected) { $raw = [Console]::In.ReadToEnd() } } catch { $raw = '' }
    $cmd = ''
    try { $cmd = [string](($raw | ConvertFrom-Json).tool_input.command) } catch { $cmd = '' }
    if ($cmd -notmatch '(^|[\s;&|(''"])aside(\.exe)?(\s|$)') { exit 0 }
}

# Fast path. A hook runs before every aside command, so it only checks the
# processes; the interactive call also confirms that the CLI answers.
if (Test-Running) {
    if ($Hook) { exit 0 }
    if (Test-Cli) { Out-One 'already running - windows untouched'; exit 0 }
}

# Never launch a second time while the browser is up: Aside.exe would open a
# new window in the running browser and the minimize loop below could hide
# the user's own windows. Wait for the daemon instead.
if (Get-Process -Name Aside) {
    $waitSw = [Diagnostics.Stopwatch]::StartNew()
    while ($waitSw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (Test-Cli) { Out-One 'browser was already open - CLI ready, windows untouched'; exit 0 }
        Start-Sleep -Milliseconds 700
    }
    Out-One 'browser is open but the CLI does not answer - restart Aside yourself'
    Stop-With 1
}

$exe = Find-AsideExe
if (-not $exe) { Out-One 'Aside.exe not found - pass -AsideExe or set ASIDE_EXE'; Stop-With 1 }

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class AgenticVaultAsideWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
}
"@

function Get-AsideWindows {
    $pids = @(Get-Process -Name Aside | ForEach-Object { $_.Id })
    $found = New-Object System.Collections.ArrayList
    if (-not $pids.Count) { return ,$found }
    $cb = [AgenticVaultAsideWin+EnumProc]{ param($h, $l)
        $p = 0; [void][AgenticVaultAsideWin]::GetWindowThreadProcessId($h, [ref]$p)
        if ($pids -contains [int]$p -and [AgenticVaultAsideWin]::IsWindowVisible($h) -and -not [AgenticVaultAsideWin]::IsIconic($h)) {
            $cn = New-Object System.Text.StringBuilder 64
            [void][AgenticVaultAsideWin]::GetClassName($h, $cn, 64)
            if ($cn.ToString() -eq 'Chrome_WidgetWin_1') { [void]$found.Add($h) }
        }
        return $true }
    [void][AgenticVaultAsideWin]::EnumWindows($cb, [IntPtr]::Zero)
    return ,$found
}

$prev = [AgenticVaultAsideWin]::GetForegroundWindow()
try { Start-Process -FilePath $exe -ErrorAction Stop } catch { Out-One "start failed: $($_.Exception.Message)"; Stop-With 1 }

# Minimize each new window as soon as it is visible. Session restore can open
# more than one window, so keep watching for three seconds after the first.
$sw = [Diagnostics.Stopwatch]::StartNew(); $firstMs = -1; $minimized = 0
while ($sw.ElapsedMilliseconds -lt 20000) {
    foreach ($h in (Get-AsideWindows)) {
        [void][AgenticVaultAsideWin]::ShowWindowAsync($h, 6)   # SW_MINIMIZE activates the next window in Z order
        $minimized++
        if ($firstMs -lt 0) { $firstMs = $sw.ElapsedMilliseconds }
    }
    if ($firstMs -ge 0 -and ($sw.ElapsedMilliseconds - $firstMs) -gt 3000) { break }
    Start-Sleep -Milliseconds 50
}
Start-Sleep -Milliseconds 300
$restored = ([AgenticVaultAsideWin]::GetForegroundWindow() -eq $prev)
if (-not $restored -and $prev -ne [IntPtr]::Zero) {
    [void][AgenticVaultAsideWin]::SetForegroundWindow($prev)
    Start-Sleep -Milliseconds 200
    $restored = ([AgenticVaultAsideWin]::GetForegroundWindow() -eq $prev)
}

$ready = $false
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    if (Test-Cli) { $ready = $true; break }
    Start-Sleep -Milliseconds 700
}
$focus = if ($restored) { 'focus restored' } else { 'focus NOT restored' }
$win = if ($firstMs -ge 0) { "window minimized ${firstMs}ms after launch ($minimized)" } else { 'no window seen' }
if ($ready) { Out-One ("started in {0:N1}s - {1} - {2}" -f $sw.Elapsed.TotalSeconds, $win, $focus); exit 0 }
Out-One ("not ready after {0}s - {1} - {2}" -f $TimeoutSec, $win, $focus)
Stop-With 1
