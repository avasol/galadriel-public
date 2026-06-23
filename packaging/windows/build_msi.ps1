# build_msi.ps1 - author a proper Aedelgard Body .msi from the PyInstaller onedir.
#
# Runs on the windows-latest GitHub Actions runner (or any Windows box with the
# .NET SDK). ASCII-only on purpose: PowerShell on the runner mangles non-ASCII
# bytes (em-dashes etc.) into parser errors.
#
# Steps:
#   1. install the WiX v4 CLI as a dotnet tool
#   2. build the .msi from aedelgard.wxs (which harvests the payload via the
#      File element's directory harvest at build time)
#   3. (optional) Authenticode-sign the .msi if a cert is provided
#
# Inputs (env):
#   BODY_VERSION           e.g. 1.17.0   (defaults to 0.0.0 for dev builds)
#   WINDOWS_CERT_BASE64    base64 of a .pfx code-signing cert  (optional)
#   WINDOWS_CERT_PASSWORD  password for that .pfx              (optional)
#
# Output: dist\Aedelgard-Body-Setup.msi
$ErrorActionPreference = "Stop"

$version = if ($env:BODY_VERSION) { $env:BODY_VERSION } else { "0.0.0" }
Write-Host "Building Aedelgard Body MSI v$version"

$payload = "dist\aedelgard-body"
if (-not (Test-Path $payload)) {
    throw "PyInstaller output not found at $payload (run pyinstaller first)."
}

# 1. WiX v4 CLI + the Util extension (provides the <Files> directory-harvest element).
dotnet tool install --global wix --version 4.0.6
$env:PATH += ";$env:USERPROFILE\.dotnet\tools"
wix extension add --global WixToolset.Util.wixext/4.0.6

# 2. Build the MSI. The payload directory is harvested by WiX at build time via
#    the Files element in aedelgard.wxs (WiX v4 has built-in harvesting; the
#    standalone heat tool from v3 is gone). PayloadDir is passed as a bind var.
wix build `
    "packaging\windows\aedelgard.wxs" `
    -ext WixToolset.Util.wixext `
    -d "ProductVersion=$version" `
    -b "PayloadDir=$payload" `
    -out "dist\Aedelgard-Body-Setup.msi"

if (-not (Test-Path "dist\Aedelgard-Body-Setup.msi")) {
    throw "wix build did not produce the MSI."
}
Write-Host "MSI built: dist\Aedelgard-Body-Setup.msi"

# 3. Optional Authenticode signing - stops the SmartScreen "Unknown Publisher"
#    scare. Skipped silently if no cert is configured, so unsigned dev builds
#    still succeed (they just warn on the user's machine).
if ($env:WINDOWS_CERT_BASE64) {
    Write-Host "Signing MSI with provided certificate..."
    $pfx = "$env:RUNNER_TEMP\codesign.pfx"
    [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_CERT_BASE64))
    $signtool = (Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
                 Sort-Object FullName | Select-Object -Last 1).FullName
    & $signtool sign `
        /f $pfx `
        /p $env:WINDOWS_CERT_PASSWORD `
        /fd SHA256 `
        /tr "http://timestamp.digicert.com" `
        /td SHA256 `
        "dist\Aedelgard-Body-Setup.msi"
    Remove-Item $pfx -Force
    Write-Host "MSI signed."
} else {
    Write-Host "No WINDOWS_CERT_BASE64 set - shipping UNSIGNED (SmartScreen will warn)."
}
