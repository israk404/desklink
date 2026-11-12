# DeskLink

A Windows tool and an Android app that work as a pair over your Wi-Fi.

| Mode on the PC | What it does |
|---|---|
| **Photo Fix** | Drop a phone photo of a printed passport photo, drag the four corners onto it, and it is flattened into a straight picture. Brightness, contrast, warmth, fine straighten, rotate, then *Save as*. |
| **Text Capture** | Click **Open phone camera**: the phone's camera opens, you take a photo of any text, and it appears on the PC with a box on every word. Click a word, drag across words, or double-click a line to copy. You can also drop or paste any picture. |
| **Send to Phone** | Drop files on the window. They arrive in DeskLink on the phone, where each one has **Open**, **Print**, **Share** and **Save**. |

## Get it

- **PC:** download [DeskLink-Windows.zip](downloads/DeskLink-Windows.zip?raw=true), right-click → **Extract All…**,
  then run `DeskLink.exe` inside the folder. Nothing to install; keep the folder together.
- **Phone:** download [DeskLink.apk](downloads/DeskLink.apk?raw=true) on the phone and open it to install
  (Android 8.0 or newer). APK signing certificate SHA-256:
  `5ed9f655232825479f8e15522bfb56f49231dee8136976e90e315ed56ebdaf9e`

The workflow publishes a signed APK only when the repository has two secrets (Settings → Secrets and variables
→ Actions): `ANDROID_KEYSTORE_BASE64` and `ANDROID_KEYSTORE_PASSWORD` (key alias `desklink`). Keep using the
same key, or the phone will refuse updates until the app is uninstalled.

## First run

1. Put the PC and the phone on the same Wi-Fi (not a guest network).
2. On the PC, start DeskLink and click **Pair phone**. Allow it through Windows Firewall when asked.
3. On the phone, open DeskLink and tap **Find my PC**. Check both screens show the same code, then click **Allow** on the PC.

## Good to know

- **Camera:** the camera opens by itself while DeskLink is on the phone's screen. Otherwise you get a
  notification to tap. To have it open any time, turn on *Let the PC open the camera any time* in the app.
- **Printing:** PDFs and pictures print through Android's own print screen, so any printer the phone can
  see works. Other file types open in their own app.
- **Text reading** uses the reader built into Windows 10/11 and reads English. If nothing is found, add
  *English (United States)* under Settings → Time & language → Language & region.
- **Privacy:** everything stays on your network. Each connection is encrypted (AES-256-GCM) with a key
  that only your PC and your phone hold.

## Layout

- `windows/`: the PC app (Python + PySide6). `python -m desklink` runs it; `tests/test_headless.py` tests it with a simulated phone.
- `android/`: the phone app (Kotlin, no third-party libraries).
