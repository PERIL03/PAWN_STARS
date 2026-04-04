param(
    [string]$Target = "",
    [switch]$NoVerify
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootPythonScript = Join-Path $scriptDir "build_rust.py"

$cmd = @($rootPythonScript)
if ($Target -ne "") {
    $cmd += "--target"
    $cmd += $Target
}
if ($NoVerify) {
    $cmd += "--no-verify"
}

python @cmd
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
