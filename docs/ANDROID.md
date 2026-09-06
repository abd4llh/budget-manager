# Android client

The Android client is native Jetpack Compose and communicates with `/api/mobile/v1/` on the selected Budget Manager server.

## Build requirements

- Android SDK 35
- JDK 17
- Windows PowerShell builder supplied in `android/build-debug.ps1`, or an equivalent Gradle 8.9 environment

Android Studio may bundle a newer Java runtime. The supplied build script deliberately selects **JDK 17** and rejects an incompatible bundled JBR.

## Debug build

```powershell
cd android
powershell -ExecutionPolicy Bypass -File .\build-debug.ps1
```

Output:

```text
android\app\build\outputs\apk\debug\app-debug.apk
```

## Release signing

Create one private signing keystore and protect/back it up permanently. Android will only accept future updates signed by the same key.

Set:

```powershell
$env:BUDGET_ANDROID_KEYSTORE='C:\secure\budget-manager-release.jks'
$env:BUDGET_ANDROID_KEY_ALIAS='budget-manager'
$env:BUDGET_ANDROID_KEYSTORE_PASSWORD='...'
$env:BUDGET_ANDROID_KEY_PASSWORD='...'
.\build-release.ps1
```

Never add the keystore or passwords to Git, GitHub Actions logs, release archives or Docker images.

## Network behavior

The app allows cleartext HTTP so it can connect to local self-hosted IP addresses. That capability should not be interpreted as permission to expose a server publicly over HTTP. Use HTTPS or a trusted VPN for remote access.
