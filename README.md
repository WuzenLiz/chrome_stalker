# Chrome Stalker
[![Build Windows Agents](https://github.com/WuzenLiz/chrome_stalker/actions/workflows/build.yml/badge.svg?branch=main&event=build)](https://github.com/WuzenLiz/chrome_stalker/actions/workflows/build.yml)

A Windows-only foreground capture agent triggered by user input events, designed as a technical proof-of-concept for Win32 automation, event orchestration, and inter-process integrations.

## Overview

This project is a Windows-native capture agent that monitors:

- Foreground Chrome windows

- Specific page titles (via regex)

- User input events (keyboard Enter key)

When conditions are met, the agent captures the Chrome window and publishes the result via Redis, allowing downstream consumers (e.g. Telegram bot, storage worker, analytics pipeline) to process the image asynchronously.

This repository is intended as:

- A technical PoC

- A systems / Win32 automation demo

- A reference implementation for event-driven capture on Windows
## Key Features

✅ Foreground window detection (Win32 API)

✅ Chrome process validation

✅ Title-based filtering (regex)

✅ Keyboard hook (Enter-triggered capture)

✅ DPI-aware, multi-monitor safe capture

✅ Event-driven architecture

✅ Redis pub/sub integration

✅ Runtime configuration via Windows Registry

✅ Thread-safe orchestration

✅ Designed for EXE packaging (PyInstaller)

## Architecture
The agent consists of the following components:

```
+-------------------+
| Keyboard Listener |
+---------+---------+
          |
          v
+-------------------+       +------------------+
| Event Queue       | ----> | Orchestrator     |
+-------------------+       | (State Machine)  |
                            +--------+---------+
                                     |
                                     v
                            +------------------+
                            | Capture Engine   |
                            | (Win32 + MSS)    |
                            +--------+---------+
                                     |
                                     v
                            +------------------+
                            | Redis Publisher  |
                            +------------------+
```

## Configuration

### Environment Variables
``` .env
REDIS_CONNECTION=redis://localhost:6379/0
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

> These are typically injected at runtime or bundled during build.


## Windows Registry Configuration

The agent reads runtime configuration from:
``` markdown
HKEY_CURRENT_USER
└── Software
    └── tBotAgent
        └── v1
```

|Key|Type|Description|Default|
|---|----|-----------|-------|
|enabled|DWORD|Enable / disable capture|1|
|interval_sec|DWORD|Minimum seconds between captures|5|
|title_regex|STRING|Regex applied to window title|facebook|messenger|zalo|
|fg_poll_interval|DWORD|Foreground polling interval (sec)|1|

Registry config can be updated without restarting the process (future extension).

## Trigger Logic

A capture is triggered when all conditions are met:

1. Foreground window belongs to chrome.exe

2. Window title matches configured regex

3. User presses Enter
4. Capture interval cooldown has passed

This avoids:

- Poll-based false positives

- Tab switching noise

- UI animation race conditions

## Output

Captured images are saved to:
```shell
%APPDATA%\captures
```

And published to Redis:
```json
Channel: IMAGE_READY
Payload:
{
  "hwnd": 123456,
  "path": "C:\\Users\\...\\capture_20260115-185500_hwnd123456.png",
  "timestamp": "20260115-185500"
}
```

## Build (Local)

This project is designed to be built locally on Windows.

Requirements

- Windows 10+

- Python 3.10+

- Visual C++ Runtime

- Chrome installed

Build EXE
``` powershell
pip install -r requirements.txt

pyinstaller ^
  --onefile ^
  --noconsole ^
  --name chrome-capture-agent ^
  main.py
```

## Non-Goals

This project does not aim to:

- Bypass security software

- Evade EDR/AV

- Persist silently

- Perform background surveillance

It is intentionally scoped as a technical automation PoC.

## Disclaimer

This code is provided for educational and research purposes only.

The author is not responsible for misuse, policy violations, or unintended consequences resulting from deploying this software in production environments.

## License

MIT License
