param(
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [string]$OutDir = "qa_snapshots"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyScript = Join-Path $scriptDir "capture_responsive.py"

python $pyScript --base-url $BaseUrl --out-dir $OutDir
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
