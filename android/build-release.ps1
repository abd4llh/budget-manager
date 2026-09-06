$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$required = @('BUDGET_ANDROID_KEYSTORE','BUDGET_ANDROID_KEY_ALIAS','BUDGET_ANDROID_KEYSTORE_PASSWORD','BUDGET_ANDROID_KEY_PASSWORD')
foreach ($name in $required) {
    if (-not (Get-Item "Env:$name" -ErrorAction SilentlyContinue).Value) { throw "Missing environment variable: $name" }
}
if (-not (Test-Path $env:BUDGET_ANDROID_KEYSTORE)) { throw "Keystore not found: $env:BUDGET_ANDROID_KEYSTORE" }

# Reuse the JDK/Gradle bootstrap logic from the debug builder without installing an APK.
function Get-JavaMajor([string]$home) {
    $java = Join-Path $home 'bin\java.exe'
    if (-not (Test-Path $java)) { return $null }
    $out = (& $java -version 2>&1 | Out-String)
    if ($out -match 'version\s+"(\d+)') { return [int]$Matches[1] }
    return $null
}
$candidates=@(); if($env:JAVA_HOME){$candidates+=$env:JAVA_HOME}
foreach($base in @('C:\Program Files\Eclipse Adoptium','C:\Program Files\Microsoft','C:\Program Files\Java')){
    if(Test-Path $base){$candidates+=(Get-ChildItem $base -Directory -ErrorAction SilentlyContinue|Where-Object{$_.Name -match 'jdk-17'}|ForEach-Object FullName)}
}
$candidates+='C:\Program Files\Android\Android Studio\jbr'
$jdk=$null; foreach($candidate in ($candidates|Select-Object -Unique)){if((Get-JavaMajor $candidate)-eq 17){$jdk=$candidate;break}}
if(-not $jdk){throw 'JDK 17 was not found.'}
$env:JAVA_HOME=$jdk; $env:Path="$env:JAVA_HOME\bin;$env:Path"

$gradleVersion='8.9'; $tools=Join-Path $root '.tools'; $gradleHome=Join-Path $tools "gradle-$gradleVersion"
if(-not(Test-Path(Join-Path $gradleHome 'bin\gradle.bat'))){
    New-Item -ItemType Directory -Force -Path $tools|Out-Null
    $zip=Join-Path $tools "gradle-$gradleVersion-bin.zip"
    Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tools -Force
}
$gradle=Join-Path $gradleHome 'bin\gradle.bat'
& $gradle --stop | Out-Null
& $gradle clean assembleRelease
if($LASTEXITCODE -ne 0){throw "Gradle release build failed with exit code $LASTEXITCODE."}
$apk=Join-Path $root 'app\build\outputs\apk\release\app-release.apk'
if(-not(Test-Path $apk)){throw "Release APK was not created: $apk"}
Write-Host "Signed release APK ready: $apk" -ForegroundColor Green
