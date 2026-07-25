param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $FeatureLabArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $repoRoot "scripts\feature_lab.py"

$pythonCandidates = @()
if ($env:DEEPTUTOR_PYTHON) {
    $pythonCandidates += $env:DEEPTUTOR_PYTHON
}
if ($env:CONDA_PREFIX) {
    $pythonCandidates += (Join-Path $env:CONDA_PREFIX "python.exe")
}
$pythonCandidates += (Join-Path $repoRoot ".venv\Scripts\python.exe")
$pythonCandidates += (Join-Path $env:USERPROFILE "miniconda3\python.exe")
$pythonCandidates += (Join-Path $env:USERPROFILE "anaconda3\python.exe")

$python = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
    }
}
if (-not $python) {
    throw "Python 3.11+ was not found. Set DEEPTUTOR_PYTHON to the interpreter used by DeepTutor."
}

if (-not $FeatureLabArgs -or $FeatureLabArgs.Count -eq 0) {
    $FeatureLabArgs = @("start")
}

& $python $scriptPath @FeatureLabArgs
exit $LASTEXITCODE
