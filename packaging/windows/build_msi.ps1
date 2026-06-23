# build_msi.ps1 — author a proper Aedelgard Body .msi from the PyInstaller onedir.
#
# Runs on the windows-latest GitHub Actions runner (or any Windows box with the
# .NET SDK). It:
#   1. installs the WiX v4 CLI as a dotnet tool
#   2. harvests dist\aedelgard-body\ into Files.wxs (the hundreds of bundled
#      files incl. the ONNX embedding model) via `wix extension heat`
#   3. builds the .msi from aedelgard.wxs + Files.wxs
#   4. (optional) Authenticode-signs the .msi if a cert is provided
#
# Inputs (env):
#   BODY_VERSION         e.g. 1.17.0   (defaults to 0.0.0 for dev builds)
#   WINDOWS_CERT_BASE64  base64 of a .pfx code-signing cert  (optional)
#   WINDOWS_CERT_PASSWORD  password for that .pfx            (optional)
#
# Output: dist\Aedelgard-Body-Setup.msi
$ErrorActionPreference = "Stop"

$version = if ($env:BODY_VERSION) { $env:BODY_VERSION } else { "0.0.0" }
Write-Host "Building Aedelgard Body MSI v$version"

$payload = "dist\aedelgard-body"
if (-not (Test-Path $payload)) {
    throw "PyInstaller output not found at $payload — run pyinstaller first."
}

# 1. WiX v4 CLI + the UI/util extensions.
dotnet tool install --global wix --version 4.*
$env:PATH += ";$env:USERPROFILE\.dotnet\tools"
wix extension add -g WixToolset.Heat

# 2. Harvest the onedir into a ComponentGroup named HarvestedFiles, rooted at
#    INSTALLFOLDER, with stable GUIDs so upgrades are clean.
wix extension run -- heat dir $payload `
    -cg HarvestedFiles `
    -dr INSTALLFOLDER `
    -gg -g1 -sfrag -srd -scom -sreg `
    -var var.PayloadDir `
    -out "packaging\windows\Files.wxs"

# 3. Build the MSI.
wix build `
    "packaging\windows\aedelgard.wxs" `
    "packaging\windows\Files.wxs" `
    -d "ProductVersion=$version" `
    -d "PayloadDir=$payload" `
    -ext WixToolset.UI.wixext `
    -out "dist\Aedelgard-Body-Setup.msi"

Write-Host "MSI built: dist\Aedelgard-Body-Setup.msi"

# 4. Optional Authenticode signing — this is what stops the SmartScreen
#    "Unknown Publisher" scare. Skipped silently if no cert is configured, so
#    unsigned dev builds still succeed (they just warn on the user's machine).
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
    Write-Host "No WINDOWS_CERT_BASE64 set — shipping UNSIGNED (SmartScreen will warn)."
}
