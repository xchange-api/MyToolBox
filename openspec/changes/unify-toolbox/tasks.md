## 1. Project Scaffold

- [x] 1.1 Create directory structure: `core/`, `plugins/`, and all subpackages with `__init__.py`
- [x] 1.2 Create `requirements.txt` with pystray, Pillow, pywin32
- [x] 1.3 Create initial `config.json` with `general` section and default plugin config sections
- [x] 1.4 Create `MyToolBox.spec` (PyInstaller config, `--noconsole`)

## 2. Core Infrastructure

- [x] 2.1 Implement `core/logger.py` — unified logging with log levels (debug/info/warning/error), stdout + file output
- [x] 2.2 Implement `core/config_manager.py` — read/write config.json, JSON error fallback, merge with plugin defaults
- [x] 2.3 Implement `plugins/base.py` — `Plugin` abstract base class with all lifecycle methods and TrayMenuItem helper
- [x] 2.4 Implement `core/plugin_manager.py` — scan `plugins/` directories, import plugins by PLUGIN attribute, manage lifecycle start/stop, error isolation

## 3. Core Application

- [x] 3.1 Implement `core/tray_manager.py` — single 64x64 tray icon, categorized menu building from plugin registrations, real-time menu updates, toggle handler dispatching
- [x] 3.2 Implement `core/ipc_server.py` — Named Pipe server (`\\.\pipe\MyToolBox\{name}_{pid}`), JSON-line protocol, multi-client support, heartbeat timeout detection (15s)
- [x] 3.3 Implement `core/app.py` — `MyToolBoxApp` class that initializes all core components, manages the main loop, routes shutdown cleanup
- [x] 3.4 Implement `main.py` — entry point: detect `--helper` flag to route to helper mode vs normal Core mode, auto-restart on first run with `--helper` detection

## 4. Plugin: InputStateNotifier

- [x] 4.1 Port `plugins/input_state_notifier/popup.py` — PopupWindow class: layered window, per-pixel alpha blending, queue-based toast rendering
- [x] 4.2 Port `plugins/input_state_notifier/notifier.py` — keyboard hook (WH_KEYBOARD_LL), IME detection (ImmGetDefaultIMEWnd/SendMessage), foreground event hook (EVENT_SYSTEM_FOREGROUND), debounce logic
- [x] 4.3 Implement `plugins/input_state_notifier/plugin.py` — Plugin subclass wiring notifier to lifecycle, registering toggle menu item in "系统工具" category
- [x] 4.4 Implement `plugins/input_state_notifier/__init__.py` — expose PLUGIN attribute

## 5. Plugin: SGuardLimiter

- [x] 5.1 Port `plugins/sguard_limiter/limiter.py` — process detection (CreateToolhelp32Snapshot), Job Object creation, process-level fallback limits (priority/affinity/I/O), thread throttling, periodic re-application
- [x] 5.2 Implement `plugins/sguard_limiter/plugin_helper.py` — `--helper` mode entry: connect to Core via Named Pipe, run limiter loop, report status via IPC, respond to stop command
- [x] 5.3 Implement `plugins/sguard_limiter/plugin.py` — Plugin subclass with `needs_admin=True`, triggers UAC elevation on start, manages IPC server side (launch helper, receive status)
- [x] 5.4 Implement `plugins/sguard_limiter/__init__.py` — expose PLUGIN attribute

## 6. Integration & Testing

- [x] 6.1 Verify full flow: app starts without UAC, InputStateNotifier works immediately (verified imports + init)
- [ ] 6.2 Verify IPC: helper connects, status updates flow back to menu, stop command works (requires UAC at runtime)
- [ ] 6.3 Verify error resilience: config.json corruption, plugin import failure, helper crash recovery (manual test)
- [ ] 6.4 Verify PyInstaller build works and resulting EXE behaves identically (manual test)
