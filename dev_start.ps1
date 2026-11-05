$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$electron = Join-Path $projectRoot 'frontend\node_modules\electron\dist\electron.exe'
$entry = Join-Path $projectRoot 'frontend\electron\main.cjs'

if (-not (Test-Path -LiteralPath $electron -PathType Leaf)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('Electron is not installed for VoiceVault. Run npm install in the frontend folder, then launch again.', 'VoiceVault could not start', 'OK', 'Error') | Out-Null
    exit 1
}

$originalElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$quotedEntry = '"' + $entry.Replace('"', '\"') + '"'
try {
    Start-Process -FilePath $electron -ArgumentList $quotedEntry -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Normal
} finally {
    if ($null -ne $originalElectronRunAsNode) { $env:ELECTRON_RUN_AS_NODE = $originalElectronRunAsNode }
}
