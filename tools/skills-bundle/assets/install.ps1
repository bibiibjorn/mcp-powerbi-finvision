<#
  Installs the PBI + Fabric skills bundle into ~/.claude/.
  Non-destructive: existing same-named items are backed up before copy.
#>
[CmdletBinding()]
param(
    [string]$ClaudeHome = (Join-Path $env:USERPROFILE ".claude")
)

$ErrorActionPreference = "Stop"
$bundleRoot = $PSScriptRoot

Write-Host ""
Write-Host "PBI + Fabric skills installer" -ForegroundColor Cyan
Write-Host "Target: $ClaudeHome"
Write-Host ""

$stamp     = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $ClaudeHome "backups\skills-bundle-import\$stamp"

$counts = @{ installed = 0; backed_up = 0 }

function Install-Tree {
    param([string]$SubDir)

    $srcBase = Join-Path $bundleRoot $SubDir
    if (-not (Test-Path $srcBase)) { return }
    $dstBase = Join-Path $ClaudeHome $SubDir
    New-Item -ItemType Directory -Force -Path $dstBase | Out-Null

    # Top-level items: skill folders, or individual agent/rule files.
    Get-ChildItem -LiteralPath $srcBase | ForEach-Object {
        $name = $_.Name
        $dst  = Join-Path $dstBase $name

        if (Test-Path -LiteralPath $dst) {
            $bdst = Join-Path $backupDir (Join-Path $SubDir $name)
            New-Item -ItemType Directory -Force -Path (Split-Path $bdst) | Out-Null
            Move-Item -LiteralPath $dst -Destination $bdst -Force
            $counts.backed_up++
            Write-Host "  backed up existing $SubDir/$name" -ForegroundColor Yellow
        }

        if ($_.PSIsContainer) {
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
        }
        $counts.installed++
        Write-Host "  installed $SubDir/$name" -ForegroundColor Green
    }
}

Install-Tree "skills"
Install-Tree "agents"
Install-Tree "rules"

Write-Host ""
Write-Host "Done. $($counts.installed) item(s) installed, $($counts.backed_up) backed up." -ForegroundColor Cyan
if ($counts.backed_up -gt 0) {
    Write-Host "Backups: $backupDir"
}
Write-Host ""
Write-Host "NEXT: set up the powerbi-finvision MCP server (see README.md) so the PBI" -ForegroundColor Cyan
Write-Host "skills can actually connect to Power BI Desktop. Then restart Claude Code."
