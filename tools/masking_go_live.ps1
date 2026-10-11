# Masking on demand - switch this PC over and check that everything is ready.
# Run it yourself in a terminal (not from a chat):
#   powershell -ExecutionPolicy Bypass -File "<repo>\tools\masking_go_live.ps1"
# It changes: the Claude Code user settings (every chat back to direct, masking hooks kept / updated),
# config\local_config.json (mcp_masking on, routing off), and it stops the old gateway.
# Every file it changes is backed up first by setup. Undo: venv\Scripts\python.exe -m client_gateway setup --remove --yes

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo "venv\Scripts\python.exe"
$fails = 0

function Pass($text) { Write-Host "  PASS  $text" -ForegroundColor Green }
function Fail($text) { Write-Host "  FAIL  $text" -ForegroundColor Red; $script:fails++ }
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

if (-not (Test-Path $py)) { Write-Host "No venv python at $py - run 'MCP Server Setup.bat' first." -ForegroundColor Red; exit 1 }
Set-Location $repo

# the gateway port (env / config / default), asked of the code that uses it
$port = 8890
try {
    $portRaw = (& $py -c "from client_gateway.control_client import gateway_port; print(gateway_port())" 2>$null | Select-Object -Last 1)
    if ($portRaw -match "^\d+$") { $port = [int]$portRaw }
} catch { }

Step "1. setup --yes (hooks, config, every chat back to direct)"
& $py -m client_gateway setup --yes
if ($LASTEXITCODE -eq 0) { Pass "setup applied" } else { Fail "setup returned $LASTEXITCODE (see above)" }

Step "2. stop the old gateway (it is older code)"
& $py -m client_gateway stop
Start-Sleep -Seconds 2

Step "3. checks"
# 3a. no gateway running
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$port/__gateway/health" -TimeoutSec 2 -UseBasicParsing | Out-Null
    Fail "a gateway still answers on port $port (run step 2 again, or close the chat that has masking on)"
} catch { Pass "no gateway running (it starts only when you type masking on)" }

# 3b. user settings: not routed any more, hooks present with the right timeouts
$cfgDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".claude" }
$settingsPath = Join-Path $cfgDir "settings.json"
$settings = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$url = $settings.env.ANTHROPIC_BASE_URL
if ($url -and $url -match "127\.0\.0\.1|localhost") { Fail "user settings still send every chat to the gateway ($url)" }
else { Pass "user settings: chats go direct ($(if ($url) { $url } else { 'no base URL set' }))" }

$want = @{ "SessionStart" = @("session", 120); "UserPromptSubmit" = @("prompt", 120); "PreToolUse" = @("pretool", 30) }
foreach ($event in $want.Keys) {
    $word, $timeout = $want[$event]
    $found = $null
    foreach ($group in @($settings.hooks.$event)) {
        foreach ($h in @($group.hooks)) {
            if ($h.command -match "client_masking_hook\.py" -and $h.command -match "\b$word\b") { $found = $h }
        }
    }
    if (-not $found) { Fail "hook $event ($word) missing" }
    elseif ([int]$found.timeout -lt $timeout) { Fail "hook $event timeout $($found.timeout) s (needs $timeout)" }
    else { Pass "hook $event ($word), timeout $($found.timeout) s" }
}

# 3c. this install's config
$local = Join-Path $repo "config\local_config.json"
if (Test-Path $local) {
    $lc = Get-Content $local -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($lc.client_gateway.mcp_masking -eq $true) { Pass "config: mcp_masking = true" } else { Fail "config: mcp_masking is not true" }
    if ($lc.client_gateway.routing -eq $true) { Fail "config: routing = true (the gateway would start with every MCP)" } else { Pass "config: routing = false (Copilot/ChatGPT not routed)" }
} else { Fail "no config\local_config.json" }

# 3d. no project folder still routed from an earlier masking on
$reg = Join-Path $env:LOCALAPPDATA "FinvisionMasking\gateway_folders.json"
if (Test-Path $reg) {
    $folders = (Get-Content $reg -Raw -Encoding UTF8 | ConvertFrom-Json).folders
    $left = @()
    foreach ($p in $folders.PSObject.Properties) {
        if ($p.Name -eq "*") { continue }
        $f = Join-Path $p.Value.path ".claude\settings.local.json"
        if (Test-Path $f) {
            $u = (Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json).env.ANTHROPIC_BASE_URL
            if ($u -match "127\.0\.0\.1:$port\b") { $left += $p.Value.path }
        }
    }
    if ($left.Count -gt 0) { Fail ("still routed (type masking off in a chat there): " + ($left -join ", ")) }
    else { Pass "no project folder is routed" }
} else { Pass "no project folder is routed" }

# 3e. the offline self-test of the masking code
Step "4. self-test (offline replay of the recorded app traffic)"
& $py tools\masking_lab\lab.py selftest
if ($LASTEXITCODE -eq 0) { Pass "self-test" } else { Fail "self-test returned $LASTEXITCODE" }

Step "Result"
if ($fails -eq 0) {
    Write-Host "  READY." -ForegroundColor Green
} else {
    Write-Host "  $fails check(s) failed - see the FAIL lines above." -ForegroundColor Red
}
Write-Host ""
Write-Host "Then, by hand:"
Write-Host "  1. VS Code: Ctrl+Shift+P > 'Developer: Reload Window' (the Power BI MCP loads the new code)."
Write-Host "  2. New chat: type  masking on  and wait for 'Client masking ON for this chat' (first start up to ~30 s)."
Write-Host "  3. Ask: 'Spell the first customer name of R0201 letter by letter' - you should see a code (C-U-S-_...)."
Write-Host "  4. Type  masking off  when done; about half a minute later no gateway runs."
exit $fails
