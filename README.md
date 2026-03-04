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

✅ Redis Streams integration (at-least-once delivery)

✅ Runtime configuration via Windows Registry

✅ Thread-safe orchestration

✅ Designed for EXE packaging (PyInstaller)

✅ Windows Services via NSSM (auto-restart, log redirection)

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
                            | (Streams / xadd) |
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
|title_regex|STRING|Regex applied to window title|`facebook\|messenger\|zalo`|
|fg_poll_interval|DWORD|Foreground polling interval (sec)|1|
|stream_name|STRING|Redis Stream name|IMAGE_STREAM|
|stream_consumer_group|STRING|Redis consumer group|tbot_group|
|stream_maxlen|DWORD|Max entries kept in stream|1000|
|circuit_breaker_threshold|DWORD|Consecutive Telegram failures before pause|5|
|circuit_breaker_sleep_sec|STRING|Pause duration (sec) when circuit opens|60.0|

Registry config is re-read every few seconds without restarting the process.

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
%APPDATA%\chrome_stalker\captures
```

And published to a Redis Stream:
```json
Stream: IMAGE_STREAM
Message fields:
{
  "type": "IMAGE_READY",
  "v": "1",
  "hwnd": "123456",
  "path": "C:\\Users\\...\\capture_20260115-185500_hwnd123456.png",
  "timestamp": "20260115-185500"
}
```

## Process Management (NSSM)

Both agents run as Windows Services managed by
[NSSM](https://nssm.cc) (Non-Sucking Service Manager).

### First-time setup

1. Download `nssm.exe` from <https://nssm.cc/download> and either:
   - Place it in this directory, **or**
   - Add it to your `PATH`

2. Open an **Administrator** command prompt and run:

```bat
nssm_install.bat
```

This installs `ChromeStalker` and `ChromeTBot` as auto-start Windows Services
with log files in the `logs\` directory.

### Daily operations

| Action | Command (Admin prompt) |
|--------|------------------------|
| Start both services | `start.bat` |
| Stop both services | `stop.bat` |
| Check service status | `nssm status ChromeStalker` |
| View logs | `logs\stalker.log`, `logs\tbot.log` |
| Remove services | `nssm_uninstall.bat` |

### Service names

| Service | Script |
|---------|--------|
| `ChromeStalker` | `stalker.py` |
| `ChromeTBot` | `tBotAgent.py` |

## Build (Local)

This project is designed to be built locally on Windows.

Requirements

- Windows 10+

- Python 3.10+

- Visual C++ Runtime

- Chrome installed

- [NSSM](https://nssm.cc) (for service management)

Install dependencies:
``` powershell
pip install -r requirements.txt
```

Build EXE (optional):
``` powershell
pyinstaller ^
  --onefile ^
  --noconsole ^
  --name chrome-capture-agent ^
  stalker.py
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
