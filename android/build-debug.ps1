$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Get-JavaMajor([string]$home) {
    $java = Join-Path $home 'bin\java.exe'
    if (-not (Test-Path $java)) { return $null }
    $out = (& $java -version 2>&1 | Out-String)
    if ($out -match 'version\s+"(\d+)') { return [int]$Matches[1] }
    return $null
}

$candidates = @()
if ($env:JAVA_HOME) { $candidates += $env:JAVA_HOME }
foreach ($base in @('C:\Program Files\Eclipse Adoptium','C:\Program Files\Microsoft','C:\Program Files\Java')) {
    if (Test-Path $base) {
        $candidates += (Get-ChildItem $base -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'jdk-17' } | ForEach-Object FullName)
    }
}
$candidates += 'C:\Program Files\Android\Android Studio\jbr'

$jdk = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if ((Get-JavaMajor $candidate) -eq 17) { $jdk = $candidate; break }
}
if (-not $jdk) {
    throw 'JDK 17 was not found. Install Temurin 17 (winget install EclipseAdoptium.Temurin.17.JDK) or set JAVA_HOME to a JDK 17 installation.'
}
$env:JAVA_HOME = $jdk
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
Write-Host "Using JDK 17: $env:JAVA_HOME"
& (Join-Path $env:JAVA_HOME 'bin\java.exe') -version

if (-not $env:ANDROID_HOME) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
    if (Test-Path $candidate) { $env:ANDROID_HOME = $candidate }
}
if (-not $env:ANDROID_HOME -or -not (Test-Path $env:ANDROID_HOME)) {
    throw 'Android SDK not found. Install Android Studio first or set ANDROID_HOME.'
}

$gradleVersion = '8.9'
$tools = Join-Path $root '.tools'
$gradleHome = Join-Path $tools "gradle-$gradleVersion"
if (-not (Test-Path (Join-Path $gradleHome 'bin\gradle.bat'))) {
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $zip = Join-Path $tools "gradle-$gradleVersion-bin.zip"
    Write-Host "Downloading Gradle $gradleVersion..."
    Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tools -Force
}

$gradle = Join-Path $gradleHome 'bin\gradle.bat'
& $gradle --stop | Out-Null
& $gradle clean assembleDebug
if ($LASTEXITCODE -ne 0) { throw "Gradle build failed with exit code $LASTEXITCODE." }

$apk = Join-Path $root 'app\build\outputs\apk\debug\app-debug.apk'
if (-not (Test-Path $apk)) { throw "Build completed without the expected APK: $apk" }
Write-Host "`nAPK ready: $apk" -ForegroundColor Green

$adb = Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe'
if ((Test-Path $adb) -and ((& $adb devices) -match '\tdevice')) {
    $answer = Read-Host 'Android device detected. Install/update the APK now? [Y/n]'
    if ($answer -eq '' -or $answer -match '^[Yy]') {
        & $adb install -r $apk
        if ($LASTEXITCODE -ne 0) { throw 'ADB installation failed.' }
    }
}
