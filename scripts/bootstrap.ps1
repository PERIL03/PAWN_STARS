param(
    [switch]$WithRust,
    [string]$Python = "python"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyScript = Join-Path $scriptDir "bootstrap.py"

$cmd = @($pyScript, "--python", $Python)
if ($WithRust) {
    $cmd += "--with-rust"
}

python @cmd
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
