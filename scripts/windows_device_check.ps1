<#
.SYNOPSIS
  The real-machine check of the Anthill Windows app (docs/releasing.md, "Before the first public Windows build").

.DESCRIPTION
  Run it in PowerShell as an ordinary user (not as administrator), AFTER you installed the app from the release page:

      powershell -ExecutionPolicy Bypass -File scripts\windows_device_check.ps1            # the app
      powershell -ExecutionPolicy Bypass -File scripts\windows_device_check.ps1 -Beta      # the beta app

  It does the checks a machine can do (where the app is, how fast the window shows, whether closing or crashing the
  app leaves the backend running, whether the data is there), asks you yes/no for what only a person can see (sharp
  text, the right icon, a chat answering), and writes everything to a markdown file on your Desktop to paste on the
  release page. Step 8 (uninstall) is done by you in Settings, Apps; then run it once more with -AfterUninstall.

  It reads nothing secret (it only tests that files exist), makes no network calls except to this machine, installs
  nothing and deletes nothing. The only things it does to the app are to start it, close it politely, and end it the
  hard way (steps 5 and 6), and it asks before those two.

  Keep this file plain ASCII: Windows PowerShell 5.1 reads a script without a byte-order mark as ANSI.
#>
[CmdletBinding()]
param(
    [switch]$Beta,            # check "Anthill Beta" (the beta feed) instead of "Anthill"
    [switch]$AfterUninstall,  # step 8: run this after you removed the app in Settings, Apps
    [switch]$NonInteractive,  # ask nothing (used by CI); the questions are recorded as NOT ASKED
    [switch]$SelfTest,        # parse this file and print the machine facts, nothing else
    [string]$OutFile = ""     # where to write the result (default: your Desktop)
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

# The names below are the ones scripts/windows_installer_check.py uses in CI (tests/test_windows_device_check.py keeps them equal).
$SidecarExe = "anthill-server.exe"
$ShellExe = "anthill-desktop.exe"
$UninstallerExe = "uninstall.exe"
$ProductName = if ($Beta) { "Anthill Beta" } else { "Anthill" }
$DataFolderName = $ProductName
$LocalAppData = $env:LOCALAPPDATA
$DataDir = Join-Path $LocalAppData $DataFolderName
$ShellProcess = [IO.Path]::GetFileNameWithoutExtension($ShellExe)
$SidecarProcess = [IO.Path]::GetFileNameWithoutExtension($SidecarExe)

$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Failures = 0

function Say([string]$Text) {
    Write-Host $Text
    $script:Lines.Add($Text)
}

function Record([string]$Status, [string]$Label, [string]$Detail = "") {
    if ($Status -eq "FAIL") { $script:Failures++ }
    $line = "- **$Status** - $Label"
    if ($Detail) { $line = "$line ($Detail)" }
    Say $line
}

# The result is pasted on the public release page: show the user-profile part of a path as %LOCALAPPDATA%, not as the account name.
function Hide-Home([string]$Path) {
    if ($Path -and $LocalAppData) { return $Path.Replace($LocalAppData, "%LOCALAPPDATA%") }
    return $Path
}

function Ask([string]$Question) {
    if ($NonInteractive) {
        Record "NOT ASKED" $Question
        return
    }
    do {
        $answer = (Read-Host "$Question [y/n]").Trim().ToLower()
    } while ($answer -ne "y" -and $answer -ne "n")
    if ($answer -eq "y") {
        Record "PASS" $Question
    } else {
        $seen = Read-Host "  What did you see? (one line, Enter to skip)"
        Record "FAIL" $Question $seen
    }
}

function Pause-ForPerson([string]$Text) {
    if ($NonInteractive) { return }
    [void](Read-Host $Text)
}

function Get-ProcessesNamed([string]$Name) {
    return @(Get-Process -Name $Name -ErrorAction SilentlyContinue)
}

function Wait-Until([scriptblock]$Condition, [double]$Seconds) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt $Seconds) {
        if (& $Condition) { return [math]::Round($watch.Elapsed.TotalSeconds, 1) }
        Start-Sleep -Milliseconds 500
    }
    if (& $Condition) { return [math]::Round($watch.Elapsed.TotalSeconds, 1) }
    return $null
}

function Test-Gone([string]$Name) {
    return (@(Get-ProcessesNamed $Name).Count -eq 0)
}

# The folder a per-user install put the app in: the one that holds the backend, anthill-server.exe.
function Find-App {
    foreach ($parent in @((Join-Path $LocalAppData "Programs"), $LocalAppData)) {
        if (-not (Test-Path -LiteralPath $parent)) { continue }
        $named = Join-Path $parent $ProductName
        if (Test-Path -LiteralPath (Join-Path $named $SidecarExe)) { return $named }
        $folders = @(Get-ChildItem -LiteralPath $parent -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        foreach ($folder in $folders) {
            $isBeta = ((Split-Path $folder -Leaf) -like "*Beta*")
            if ($isBeta -ne [bool]$Beta) { continue }
            if (Test-Path -LiteralPath (Join-Path $folder $SidecarExe)) { return $folder }
        }
    }
    return $null
}

function Get-Facts {
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = @(Get-CimInstance Win32_Processor | Select-Object -First 1)[0]
    $gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { "$($_.Name) (driver $($_.DriverVersion))" })
    $ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $scale = "unknown"
    try {
        $dpi = (Get-ItemProperty "HKCU:\Control Panel\Desktop\WindowMetrics" -ErrorAction Stop).AppliedDPI
        $scale = "$([math]::Round($dpi / 96 * 100))%"
    } catch {
        $scale = "unknown"
    }
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    return [ordered]@{
        "Windows"        = "$($os.Caption) $($os.Version) (build $($os.BuildNumber))"
        "Processor"      = "$($cpu.Name)"
        "Memory"         = "$ramGb GB"
        "Graphics"       = ($gpus -join "; ")
        "Display scale"  = $scale
        "PowerShell"     = "$($PSVersionTable.PSVersion)"
        "Run as admin"   = "$isAdmin"
    }
}

function Write-Result {
    $target = $OutFile
    if (-not $target) {
        $folder = [Environment]::GetFolderPath("Desktop")
        if (-not $folder -or -not (Test-Path -LiteralPath $folder)) { $folder = $env:TEMP }
        $suffix = if ($Beta) { "-beta" } else { "" }
        $target = Join-Path $folder ("anthill-device-check-{0}{1}.md" -f (Get-Date -Format "yyyyMMdd-HHmm"), $suffix)
    }
    Set-Content -LiteralPath $target -Value $script:Lines -Encoding UTF8
    Write-Host ""
    Write-Host "Result written to $target"
    Write-Host "Paste its text on the release page (or in the issue it belongs to)."
    return $target
}

# ---- self test: the file parses and the machine facts can be read ---------------------------------------------------
if ($SelfTest) {
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $parseErrors | ForEach-Object { Write-Host "PARSE ERROR: $($_.Message) at line $($_.Extent.StartLineNumber)" }
        exit 1
    }
    Write-Host "windows-device-check: PASS - the script parses"
    (Get-Facts).GetEnumerator() | ForEach-Object { Write-Host ("windows-device-check: {0}: {1}" -f $_.Key, $_.Value) }
    exit 0
}

trap {
    Say "- **FAIL** - the check stopped on an error: $($_.Exception.Message)"
    [void](Write-Result)
    exit 1
}

Say "# Anthill Windows check: $ProductName"
Say ""
Say "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
foreach ($fact in (Get-Facts).GetEnumerator()) { Say ("- {0}: {1}" -f $fact.Key, $fact.Value) }
Say ""

# ---- step 8, after the uninstall: the app is gone, the data stays -----------------------------------------------------
if ($AfterUninstall) {
    Say "## 8. After the uninstall (Settings, Apps)"
    $left = Find-App
    $leftDetail = if ($left) { "still at $(Hide-Home $left)" } else { "none found" }
    Record $(if ($left) { "FAIL" } else { "PASS" }) "the app folder is gone" $leftDetail
    $running = @(Get-ProcessesNamed $ShellProcess).Count + @(Get-ProcessesNamed $SidecarProcess).Count
    Record $(if ($running -eq 0) { "PASS" } else { "FAIL" }) "no Anthill process is running" "$running found"
    $dataThere = Test-Path -LiteralPath $DataDir
    Record $(if ($dataThere) { "PASS" } else { "FAIL" }) "your data folder stays after the uninstall" (Hide-Home $DataDir)
    Say ""
    $failed = $script:Failures
    [void](Write-Result)
    if ($failed -gt 0) { exit 1 }
    exit 0
}

# ---- step 1: the install ------------------------------------------------------------------------------------------------
Say "## 1. Install"
$appDir = Find-App
Record $(if ($appDir) { "PASS" } else { "FAIL" }) "Anthill is installed for this user (no administrator folder)" $(if ($appDir) { Hide-Home $appDir } else { "not found under %LOCALAPPDATA%; install it first" })
if (-not $appDir) {
    Say ""
    $failed = $script:Failures
    [void](Write-Result)
    exit 1
}
$mainExe = @(Get-ChildItem -LiteralPath $appDir -Filter "*.exe" | Where-Object { $_.Name -ne $SidecarExe -and $_.Name -ne $UninstallerExe } | Sort-Object Name)
$shellPath = $null
if ($mainExe.Count -gt 0) { $shellPath = $mainExe[0].FullName }
Record $(if ($shellPath) { "PASS" } else { "FAIL" }) "the app and its backend are installed side by side" $(if ($shellPath) { "$(Split-Path $shellPath -Leaf), version $((Get-Item -LiteralPath $shellPath).VersionInfo.ProductVersion)" } else { "no program next to $SidecarExe" })
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = @(Get-ChildItem -LiteralPath $startMenu -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue | Where-Object { $_.BaseName -eq $ProductName })
Record $(if ($shortcut.Count -gt 0) { "PASS" } else { "FAIL" }) "$ProductName is in the Start menu"
Ask "Did Windows show the SmartScreen warning, and did 'Run anyway' install with no administrator prompt?"
if (-not $shellPath) {
    Say ""
    [void](Write-Result)
    exit 1
}

# ---- step 2: the window ---------------------------------------------------------------------------------------------------
Say ""
Say "## 2. The window"
$wasRunning = @(Get-ProcessesNamed $ShellProcess).Count -gt 0
if (-not $wasRunning) { Start-Process -FilePath $shellPath }
$seconds = Wait-Until { @(Get-ProcessesNamed $ShellProcess | Where-Object { $_.MainWindowTitle }).Count -gt 0 } 60
$detail = if ($wasRunning) { "it was already running, so the start time was not measured; close it and run this again to measure it" } elseif ($null -ne $seconds) { "$seconds s after starting" } else { "no window within 60 s" }
$windowStatus = if ($wasRunning) { "NOT MEASURED" } elseif ($null -ne $seconds -and $seconds -le 30) { "PASS" } else { "FAIL" }
Record $windowStatus "the window opens within about 30 seconds" $detail
$title = (Get-ProcessesNamed $ShellProcess | Where-Object { $_.MainWindowTitle } | Select-Object -First 1)
if ($title) { Say "- Window title: $($title.MainWindowTitle)" }
Ask "Is the text sharp at this screen's scale, does the window resize, and does it show the right icon and title?"
Ask "Did no console (black) window flash when it started?"

# ---- step 3: the backend and the first chat -------------------------------------------------------------------------------
Say ""
Say "## 3. First run"
$sidecars = @(Get-ProcessesNamed $SidecarProcess)
$ports = @()
if ($sidecars.Count -gt 0) {
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $sidecars.Id -contains $_.OwningProcess -and ($_.LocalAddress -eq "127.0.0.1" -or $_.LocalAddress -eq "::1") } | ForEach-Object { $_.LocalPort } | Sort-Object -Unique)
}
Record $(if ($ports.Count -gt 0) { "PASS" } else { "FAIL" }) "the backend is running and listens on this machine only" $(if ($ports.Count -gt 0) { "port $($ports -join ', ')" } else { "no loopback port found" })
$ollama = @(Get-ProcessesNamed "ollama")
if ($ollama.Count -gt 0 -and $ollama[0].Path) {
    try {
        $ps = (& $ollama[0].Path ps 2>&1 | Out-String).Trim()
        Say "- Local engine is running. ``ollama ps``:"
        Say ('  ```' + "`n" + $ps + "`n" + '  ```')
    } catch {
        Say "- Local engine is running; ``ollama ps`` could not be read."
    }
} else {
    Say "- Local engine (ollama.exe) is not running yet; the first run downloads it in the background (about 1.5 GB)."
}
Ask "Does the model picker offer models that fit this machine's memory?"
Ask "Does a chat answer once a model is installed?"

# ---- step 4: the graphics card ------------------------------------------------------------------------------------------------
Say ""
Say "## 4. NVIDIA graphics card"
$gpuNames = (Get-Facts)["Graphics"]
if ($gpuNames -match "NVIDIA") {
    $smi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
    if ($smi) {
        try {
            $card = (& $smi.Source "--query-gpu=name,driver_version,memory.total" "--format=csv,noheader" 2>&1 | Out-String).Trim()
            Say "- nvidia-smi: $card"
        } catch {
            Say "- nvidia-smi is installed but could not be read."
        }
    }
    Say "- Look at the PROCESSOR column of ``ollama ps`` above (or run it now): it should say GPU while a model is loaded."
    Ask "Is the model on the GPU, and is the answer clearly faster than on the CPU?"
} else {
    Say "- No NVIDIA card on this machine: skipped (a machine with one covers this row)."
}

# ---- step 5: close the window -----------------------------------------------------------------------------------------------
Say ""
Say "## 5. Close the window"
Pause-ForPerson "Press Enter to close Anthill now (it is closed the normal way, like the X button)"
foreach ($p in (Get-ProcessesNamed $ShellProcess)) { [void]$p.CloseMainWindow() }
$gone = Wait-Until { Test-Gone $SidecarProcess } 15
Record $(if ($null -ne $gone) { "PASS" } else { "FAIL" }) "no $SidecarExe is left a few seconds after closing" $(if ($null -ne $gone) { "gone after $gone s" } else { "still running after 15 s" })
if (-not (Test-Gone $ShellProcess)) {
    $closed = Wait-Until { Test-Gone $ShellProcess } 10
    Record $(if ($null -ne $closed) { "PASS" } else { "FAIL" }) "the app itself exits after the window is closed"
}

# ---- step 6: end the program the hard way -------------------------------------------------------------------------------------
Say ""
Say "## 6. End the program (a crash)"
Pause-ForPerson "Press Enter to start Anthill again and then end it the hard way, like Task Manager's End task"
Start-Process -FilePath $shellPath
[void](Wait-Until { @(Get-ProcessesNamed $SidecarProcess).Count -gt 0 } 60)
$backendUp = @(Get-ProcessesNamed $SidecarProcess).Count -gt 0
Record $(if ($backendUp) { "PASS" } else { "FAIL" }) "the backend came up again"
foreach ($p in (Get-ProcessesNamed $ShellProcess)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
$orphan = Wait-Until { Test-Gone $SidecarProcess } 20
Record $(if ($null -ne $orphan) { "PASS" } else { "FAIL" }) "$SidecarExe disappears by itself after the app is ended" $(if ($null -ne $orphan) { "gone after $orphan s" } else { "still running after 20 s" })
if ($null -eq $orphan) {
    foreach ($p in (Get-ProcessesNamed $SidecarProcess)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    Say "- The leftover backend was ended by this script."
}

# ---- step 7: start again, the data is still there -------------------------------------------------------------------------------
Say ""
Say "## 7. Start again"
Start-Process -FilePath $shellPath
$again = Wait-Until { @(Get-ProcessesNamed $ShellProcess | Where-Object { $_.MainWindowTitle }).Count -gt 0 } 60
Record $(if ($null -ne $again) { "PASS" } else { "FAIL" }) "it starts again" $(if ($null -ne $again) { "window after $again s" } else { "no window within 60 s" })
$secrets = Join-Path $DataDir "secrets.env"
Record $(if (Test-Path -LiteralPath $secrets) { "PASS" } else { "FAIL" }) "the data folder holds the app's data" (Hide-Home $DataDir)
Ask "Are your data and your sign-in still there?"

# ---- step 8 is yours ----------------------------------------------------------------------------------------------------------------
Say ""
Say "## 8. Uninstall"
$again8 = if ($Beta) { "-AfterUninstall -Beta" } else { "-AfterUninstall" }
Say "- Close Anthill, remove it in Settings, Apps, then run this script again with $again8."
Say ""
Say "Result: $(if ($script:Failures -eq 0) { 'no failures' } else { "$($script:Failures) failure(s)" })"
$failed = $script:Failures
[void](Write-Result)
if ($failed -gt 0) { exit 1 }
exit 0
