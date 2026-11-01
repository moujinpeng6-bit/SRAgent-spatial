param(
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Limit = 1,
    [string]$Python = "python"
)
$ErrorActionPreference = 'Stop'
if ($Limit -lt 1 -or $Limit -gt 25) { throw 'Limit must be between 1 and 25.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$keyWasSet = [bool]$env:OPENAI_API_KEY
$secureKey = $null
$credential = $null
try {
    if (-not $keyWasSet) {
        $secureKey = Read-Host 'Enter OpenAI API key (hidden; used only in this process)' -AsSecureString
        $credential = New-Object System.Net.NetworkCredential('', $secureKey)
        $env:OPENAI_API_KEY = $credential.Password
    }
    if (-not $env:OPENAI_API_KEY) { throw 'An API key is required.' }
    Push-Location $projectRoot
    try {
        & $Python -X utf8 run.py benchmark --methods rules original_agent spatial_agent --model $Model --limit $Limit --out outputs/v02/agent_benchmark.json
        if ($LASTEXITCODE -ne 0) { throw 'Benchmark is incomplete. Check its report for skipped/failed cases.' }
    } finally {
        Pop-Location
    }
} finally {
    if (-not $keyWasSet) { Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue }
    if ($secureKey) { $secureKey.Dispose() }
    $credential = $null
}
