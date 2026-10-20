# Run on an approved Windows build PC. Download ONLY the public Microsoft runtime.
# No company source code, data, credentials or telemetry is sent by this script.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendor = Join-Path $base 'vendor'
$target = Join-Path $vendor 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
$hashfile = Join-Path $vendor 'MicrosoftEdgeWebView2RuntimeInstallerX64.sha256'
New-Item -Force -ItemType Directory -Path $vendor | Out-Null

function Assert-MicrosoftInstaller([string] $file) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'WebView2 installer was not found.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(?i)O=Microsoft Corporation(?:,|$)') {
        throw 'WebView2 installer signature is not valid or is not signed by Microsoft Corporation.'
    }
    $size = (Get-Item -LiteralPath $file).Length
    if ($size -lt 10000000) { throw 'WebView2 standalone installer is unexpectedly small.' }
}

if (Test-Path -LiteralPath $target) {
    Write-Host 'Using existing offline WebView2 Runtime installer.'
    Assert-MicrosoftInstaller $target
} else {
    # Stable official Microsoft link for x64 Evergreen Standalone Runtime.
    $url = 'https://go.microsoft.com/fwlink/?linkid=2124701'
    $temp = Join-Path $vendor 'webview2.download'
    try {
        Write-Host 'Downloading signed x64 Evergreen Standalone Runtime from Microsoft...'
        Invoke-WebRequest -Uri $url -MaximumRedirection 8 -OutFile $temp -UseBasicParsing
        Assert-MicrosoftInstaller $temp
        Move-Item -LiteralPath $temp -Destination $target -Force
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}
$hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $hashfile -Value $hash -Encoding Ascii
Write-Host ("WebView2 Runtime: OK; SHA-256: " + $hash)
Write-Host 'The prerequisite will be bundled in setup and can be installed OFFLINE on client PCs.'
