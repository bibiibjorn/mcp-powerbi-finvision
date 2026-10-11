# MCP-PowerBi-Finvision dev setup - PowerShell entry point.
# The setup lives in one place, "MCP Server Setup.bat" (Python + uv, venv, requirements, Claude Desktop,
# Claude Code, VS Code / GitHub Copilot, client masking). This script only runs it, so the two can
# never drift apart again (the old copy here cloned a repo that no longer exists).
#
#   powershell -ExecutionPolicy Bypass -File .\setup-dev.ps1

$bat = Join-Path $PSScriptRoot "MCP Server Setup.bat"
if (-not (Test-Path $bat)) {
    Write-Host "MCP Server Setup.bat not found next to this script: $bat" -ForegroundColor Red
    exit 1
}
& cmd.exe /c "`"$bat`""
exit $LASTEXITCODE
