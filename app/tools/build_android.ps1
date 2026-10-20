# Builds signed APK and AAB of the Noctis app for Android.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_android.ps1 -Server http://192.168.0.136:8000
#
# -Server  address of the Noctis server baked into the app (users never type it). The phone must be able to reach it.
# Output:  Noctis-<version>.apk (install on a phone) and Noctis-<version>.aab (Google Play) in the Noctis folder.
# Needs:   Flutter (..\..\flutter), Android SDK (..\..\android-sdk), signing key android\noctis-release.jks + android\key.properties
#          (these two files are NOT in the repository - back them up).
param(
    [string]$Server = "http://192.168.0.136:8000"
)
$ErrorActionPreference = "Stop"
$app = Split-Path -Parent $PSScriptRoot
$root = (Resolve-Path (Join-Path $app "..\..")).Path
$env:ANDROID_HOME = Join-Path $root "android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:PATH = (Join-Path $root "flutter\bin") + ";" + (Join-Path $env:ANDROID_HOME "platform-tools") + ";" + $env:PATH
Set-Location $app

$version = (Select-String -Path pubspec.yaml -Pattern '^version:\s*([0-9.]+)').Matches[0].Groups[1].Value
if (-not (Test-Path "android\key.properties")) { Write-Warning "android\key.properties not found: the build will be signed with the debug key." }

flutter pub get
if ($LASTEXITCODE -ne 0) { throw "flutter pub get failed" }
flutter build apk --release "--dart-define=NOCTIS_SERVER=$Server"
if ($LASTEXITCODE -ne 0) { throw "APK build failed" }
flutter build appbundle --release "--dart-define=NOCTIS_SERVER=$Server"
if ($LASTEXITCODE -ne 0) { throw "AAB build failed" }

Copy-Item "build\app\outputs\flutter-apk\app-release.apk" (Join-Path $root "Noctis-$version.apk") -Force
Copy-Item "build\app\outputs\bundle\release\app-release.aab" (Join-Path $root "Noctis-$version.aab") -Force
Write-Host "Done: Noctis-$version.apk and Noctis-$version.aab are in $root (server baked in: $Server)"
