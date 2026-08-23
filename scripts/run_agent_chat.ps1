[CmdletBinding()]
param(
    [ValidateSet("Auto", "Mock", "Gemini")]
    [string]$Mode = "Auto",

    [string]$Query,

    [string[]]$ImageUrl = @(),

    [switch]$Trace,

    [string]$UserId = "local-powershell"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements.txt"
$chatRunner = Join-Path $PSScriptRoot "run_agent_chat.py"

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $pythonLauncher) {
            Write-Host "로컬 Python 가상환경을 생성합니다 (.venv)..."
            & $pythonLauncher.Source -3 -m venv (Join-Path $repoRoot ".venv")
        }
        else {
            $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
            if ($null -eq $pythonCommand) {
                throw "Python 3를 찾지 못했습니다. Python을 설치한 뒤 다시 실행해주세요."
            }
            Write-Host "로컬 Python 가상환경을 생성합니다 (.venv)..."
            & $pythonCommand.Source -m venv (Join-Path $repoRoot ".venv")
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Python 가상환경 생성에 실패했습니다."
        }
    }

    & $venvPython -c "import importlib.util, sys; modules = ('langgraph', 'httpx', 'pydantic_settings'); sys.exit(any(importlib.util.find_spec(name) is None for name in modules))" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "에이전트 실행 의존성을 설치합니다..."
        & $venvPython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Python 의존성 설치에 실패했습니다."
        }
    }

    $runnerArguments = @(
        $chatRunner,
        "--mode", $Mode.ToLowerInvariant(),
        "--user-id", $UserId
    )
    if ($Query) {
        $runnerArguments += @("--query", $Query)
    }
    foreach ($url in $ImageUrl) {
        $runnerArguments += @("--image-url", $url)
    }
    if ($Trace) {
        $runnerArguments += "--trace"
    }

    & $venvPython @runnerArguments
    $processExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $processExitCode
