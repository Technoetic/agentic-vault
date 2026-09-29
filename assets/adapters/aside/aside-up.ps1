# agentic-vault:adapter aside-up engine=0.17.0
<#
  aside-up.ps1 - start the Aside app for the aside CLI and hand the window
  focus straight back to the user. Windows only. ASCII only so Windows PowerShell 5.1
  reads it correctly without a byte order mark.

  Contract:
    - Idempotent. If the Aside browser and its daemon are already running, no
      window is touched (the user may be working in it).
    - Never starts the browser while one is running. A second Aside.exe would
      open a new window in the running browser.
    - When it starts Aside, each new browser window is minimized as soon as it
      appears and the window that was in the foreground before the launch is
      asked to take the focus back. The new window can hold the focus for a
      moment before that; the result line says whether the focus came back.
    - One output line, prefixed "[aside]". Exit 0 = CLI ready (or already
      running), 1 = not ready.
    - -Hook (Claude Code PreToolUse): reads the hook input on stdin and acts
      only when a command segment starts with aside / aside.exe; always exits 0
      so the hook never blocks the command.
    - A run takes about -TimeoutSec (1-45 s); after a launch one more bounded
      CLI check (at most 5 s) and short waits can follow, so a hook with a
      60 s timeout is not cut off.

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
    5. The CLI executable is also named aside.exe. The browser's file
       ProductName is "Aside"; the CLI's is "Aside CLI".
  Record the result of your own run in the vault's Aside operations guide
  before relying on these facts for another app version.
#>
param(
    [ValidateRange(1, 45)][int]$TimeoutSec = 30,
    [string]$AsideExe = '',
    [switch]$Hook,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'SilentlyContinue'
$deadline = (Get-Date).AddSeconds($TimeoutSec)
if ($CheckOnly) { $Hook = [switch]$true }   # -CheckOnly only classifies; it never launches

# A command segment (start, or after ; | & ( newline $( ) whose first token,
# after an optional & or . call operator, VAR=value prefixes and a path, is
# aside or aside.exe. "grep -i aside.exe", "git commit -m 'aside'" and
# "Stop-Process -Name Aside" do not match.
$AsideCommandPattern = @'
(?im)(?:^|[;|&(\n]|\$\()\s*(?:[&.]\s*)?(?:[A-Za-z_]\w*=\S*\s+)*(?:"[^"]*[\\/]|'[^']*[\\/]|[^\s"';|&]*[\\/])?aside(?:\.exe)?["']?(?=\s|$)
'@

function Out-One([string]$s) { Write-Output "[aside] $s" }
function Stop-With([int]$code) { if ($Hook) { exit 0 } else { exit $code } }
function Get-RemainingMs { return [int][Math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds) }

function Test-IsBrowserProcess($proc) {
    $path = $proc.Path
    if (-not $path) { return $true }   # cannot inspect (other user, elevated): never launch over it
    $product = (Get-Item -LiteralPath $path).VersionInfo.ProductName
    return ($product -eq 'Aside' -or $path -match '[\\/]Application[\\/]Aside\.exe$')
}

function Get-AsideBrowser { return @(Get-Process -Name Aside | Where-Object { Test-IsBrowserProcess $_ }) }
function Test-Daemon { return [bool](Get-Process -Name aside-daemon) }
function Test-Running { return ((Get-AsideBrowser).Count -gt 0 -and (Test-Daemon)) }

# One bounded CLI round trip. The expected output (42) is computed, so an
# error message that echoes the script cannot pass for success.
function Test-Cli([int]$ms) {
    if ($ms -le 0) { return $false }
    $cli = Get-Command aside -CommandType Application | Where-Object { $_.Source -match '\.exe$' } | Select-Object -First 1
    if (-not $cli) { return $false }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $cli.Source
    $psi.Arguments = 'repl "console.log(6*7)"'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc) { return $false }
    $out = $proc.StandardOutput.ReadToEndAsync()
    [void]$proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($ms)) { try { $proc.Kill() } catch { }; return $false }
    return ($out.Result -match '(?m)^\s*42\s*$')
}

function Wait-Ready {
    while ((Get-RemainingMs) -gt 0) {
        if (Test-Daemon) { if (Test-Cli ([Math]::Min((Get-RemainingMs), 10000))) { return $true } }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Find-AsideExe {
    # An explicit path (parameter, then ASIDE_EXE) is used alone: when it is
    # wrong the script reports it instead of starting some other install.
    $explicit = if ($AsideExe) { $AsideExe } elseif ($env:ASIDE_EXE) { $env:ASIDE_EXE } else { '' }
    $candidates = @()
    if ($explicit) { $candidates = @($explicit) }
    else {
        if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Aside\Application\Aside.exe') }
        if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'Aside\Application\Aside.exe') }
        if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Aside\Application\Aside.exe') }
    }
    foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { return $c } }
    return $null
}

if ($env:OS -ne 'Windows_NT') { Out-One 'Windows only - skipped'; Stop-With 1 }

# Defense in depth for the hook: the settings use an `if` filter, but a host
# that ignores it would call this before every shell command.
if ($Hook) {
    $raw = ''
    # Read stdin as UTF-8: [Console]::In uses the console code page and breaks
    # the JSON when the command holds non-ASCII text.
    try {
        if ([Console]::IsInputRedirected) {
            $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding($false)))
            $raw = $reader.ReadToEnd()
        }
    } catch { $raw = '' }
    $cmd = ''
    try { $cmd = [string](($raw | ConvertFrom-Json).tool_input.command) } catch { $cmd = '' }
    $isAside = ($cmd -match $AsideCommandPattern)
    if ($CheckOnly) { if ($isAside) { Write-Output 'aside-command' } else { Write-Output 'other-command' }; exit 0 }
    if (-not $isAside) { exit 0 }
}

# Fast path. The hook only checks processes; the interactive call also
# confirms that the CLI answers.
if (Test-Running) {
    if ($Hook) { exit 0 }
    if (Test-Cli ([Math]::Min((Get-RemainingMs), 10000))) { Out-One 'already running - windows untouched'; exit 0 }
}

# Serialize launches: parallel hooks must not start Aside twice.
$mutex = New-Object System.Threading.Mutex($false, 'Local\agentic-vault-aside-up')
$owned = $false
try { $owned = $mutex.WaitOne((Get-RemainingMs)) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
try {
    if (-not $owned -or (Get-AsideBrowser).Count -gt 0) {
        # Someone else is starting it, or the browser is up without an
        # answering daemon: wait, never launch over a running browser.
        if (Wait-Ready) { Out-One 'browser was already open - CLI ready, windows untouched'; exit 0 }
        Out-One 'browser is open or starting but the CLI does not answer - check Aside yourself'
        Stop-With 1
    }

    $exe = Find-AsideExe
    if (-not $exe) { Out-One 'Aside.exe not found - pass -AsideExe or set ASIDE_EXE'; Stop-With 1 }
    if ((Get-Item -LiteralPath $exe).VersionInfo.ProductName -eq 'Aside CLI') {
        Out-One 'the given path is the Aside CLI, not the Aside browser app'; Stop-With 1
    }

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
        $pids = @(Get-AsideBrowser | ForEach-Object { $_.Id })
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

    # Minimize each new window as soon as it is visible. Session restore can
    # open more than one window, so keep watching for three seconds after the
    # first one, within the time budget.
    $sw = [Diagnostics.Stopwatch]::StartNew(); $firstMs = -1; $minimized = 0
    $watchMs = [Math]::Min(20000, (Get-RemainingMs))
    while ($sw.ElapsedMilliseconds -lt $watchMs) {
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

    # Always try the CLI at least once after a launch, even if the window
    # watch used the whole budget.
    $ready = Wait-Ready
    if (-not $ready) { $ready = Test-Cli 5000 }
    $focus = if ($restored) { 'focus restored' } else { 'focus NOT restored' }
    $win = if ($firstMs -ge 0) { "window minimized ${firstMs}ms after launch ($minimized)" } else { 'no window seen' }
    if ($ready) { Out-One ("started in {0:N1}s - {1} - {2}" -f $sw.Elapsed.TotalSeconds, $win, $focus); exit 0 }
    Out-One ("not ready after {0:N1}s - {1} - {2}" -f $sw.Elapsed.TotalSeconds, $win, $focus)
    Stop-With 1
}
finally {
    if ($owned) { try { $mutex.ReleaseMutex() } catch { } }
    $mutex.Dispose()
}
