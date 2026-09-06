# Budget Manager Mobile for Android

Native Jetpack Compose client for a self-hosted Budget Manager server.

## Requirements

- Android 8.0 / API 26 or newer
- A Budget Manager server exposing Mobile API v1
- For building: Android SDK 35, Gradle 8.9 and **JDK 17**

## Connection

The app can discover Budget Manager on a local Wi-Fi network when the server's optional UDP discovery service is enabled. It also accepts a manual IP address, hostname, VPN address or HTTPS domain.

For remote access, HTTPS or a trusted private VPN is strongly recommended. Plain HTTP support exists for local-network self-hosting.

## Debug build on Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\build-debug.ps1
```

The builder intentionally requires JDK 17. It checks `JAVA_HOME`, common Temurin/Microsoft/Java install locations, and only uses Android Studio's bundled runtime when that runtime is actually Java 17.

## Signed release build

Create and protect a persistent Android signing keystore. Never commit the keystore or passwords. Set these environment variables:

```powershell
$env:BUDGET_ANDROID_KEYSTORE='C:\secure\budget-manager-release.jks'
$env:BUDGET_ANDROID_KEY_ALIAS='budget-manager'
$env:BUDGET_ANDROID_KEYSTORE_PASSWORD='...'
$env:BUDGET_ANDROID_KEY_PASSWORD='...'
.\build-release.ps1
```

The signed APK is produced at:

```text
app\build\outputs\apk\release\app-release.apk
```

Keep the signing key backed up. Future APK updates must be signed with the same key.

## Local data

The mobile API token is encrypted with a key stored in Android Keystore. Application backup is disabled by default. The account password is not stored by the app.
