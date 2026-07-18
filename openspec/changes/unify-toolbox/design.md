## Context

MyToolBox 是一个统一平台，用于承载多个 Windows 系统小工具。当前有两组独立功能需要整合：SGuardLimiter（SGuard64.exe 进程限速，需要管理员权限）和 InputStateNotifier（CapsLock/NumLock/IME 状态 Toast 通知，普通权限即可运行）。未来可能添加更多工具。

技术约束：
- 使用 Python 3，依赖 pystray / Pillow / pywin32
- 单 EXE 打包（PyInstaller），带 `--noconsole`
- 仅在 Windows 上运行
- config.json 作为唯一配置来源，用户手动编辑

## Goals / Non-Goals

**Goals:**
- 提供一个统一的系统托盘应用，所有工具以插件形式注册
- 插件系统：自动扫描 `plugins/` 目录，加载插件，注册菜单项
- 权限隔离：不需要管理员权限的插件在 Core 进程内运行；需要管理员权限的插件通过 `--helper` 子进程运行（触发 UAC）
- IPC：Core 与 Admin Helper 之间通过 Named Pipe 通信，协议为 JSON-line
- 配置统一：每个插件声明自己的 `default_config` + `config_schema`，启动时合并到 `config.json`
- 输入状态通知按原样移植（CapsLock/NumLock/IME Toast 弹窗）
- SGuard 限速按原样移植（进程监控 + Job Object / 线程限制）
- 打包为单个 EXE，支持 `--helper <plugin_name>` 启动子进程模式
- UAC 弹窗仅在用户启用 admin 插件时触发

**Non-Goals:**
- 不创建图形配置界面（用户手动编辑 config.json）
- 不做自动更新机制
- 不跨平台（Windows only）
- 不提供插件热加载（启动时静态加载）
- 不提供多个 admin helper 进程之间的 IPC

## Decisions

| 决策 | 选项 | 选择 |
|------|------|------|
| IPC 协议 | Named Pipe / TCP / 共享内存 | **Named Pipe** — Windows 原生，权限模型匹配，设置简单 |
| Helper 模式 | 同一 EXE 用 `--helper` 参数 / 分离 EXE | **同一 EXE 带 `--helper`** — 减少维护负担，单文件分发 |
| 插件发现 | 显式注册 / 文件系统扫描 | **文件系统扫描 `plugins/` 子目录** — 加新插件只需丢目录，改 config.json |
| IPC 消息格式 | JSON-line / protobuf / msgpack | **JSON-line** — 简单、可调试、无需额外依赖 |
| 配置格式 | JSON / YAML / TOML | **JSON** — 用户选择，Python 标准库支持 |
| 托盘菜单构建 | 静态 / 动态由插件注册 | **插件注册** — 每个插件通过 `get_menu_items()` 贡献自己的菜单项 |
| 共享代码策略 | 抽取为 core/ 工具模块 / 各插件自包含 | **抽取为 core/ 工具模块** — Logger、Win32 helper、config reader 等复用 |

### IPC 协议设计

Named Pipe 路径：`\\.\pipe\MyToolBox\{plugin_name}`

双向 JSON-line 协议，每条消息独立一行 JSON：
```json
{"type": "handshake", "pid": 1234, "plugin": "sguard_limiter"}
{"type": "status", "running": true, "pids": [1234, 5678]}
{"type": "log", "level": "info", "message": "..."}
{"type": "command", "action": "stop"}
{"type": "command", "action": "update_config", "config": {...}}
```

Core 是服务器端，Helper 是客户端。Helper 启动后主动连接，握手成功后开始通信。

### 目录结构

```
MyToolBox/
├── main.py                     # 入口: Core 模式或 --helper 模式
├── core/
│   ├── __init__.py
│   ├── app.py                  # MyToolBoxApp: 主循环、插件加载、托盘
│   ├── plugin_manager.py       # PluginManager: 扫描、加载、生命周期
│   ├── tray_manager.py         # TrayManager: 统一托盘图标 + 菜单构建
│   ├── config_manager.py       # ConfigManager: config.json 读写 + 校验
│   ├── ipc_server.py           # IpcServer: Named Pipe 服务端 (Core 端)
│   └── logger.py               # Logger: 统一日志
├── plugins/
│   ├── __init__.py
│   ├── base.py                 # Plugin 基类
│   ├── input_state_notifier/
│   │   ├── __init__.py         # Plugin 注册代码
│   │   ├── plugin.py           # Plugin 子类实现
│   │   ├── notifier.py         # 移植: 核心 IME/键盘监控
│   │   └── popup.py            # 移植: Toast 弹窗
│   └── sguard_limiter/
│       ├── __init__.py         # Plugin 注册代码
│       ├── plugin.py           # Plugin 子类实现 (Core 端)
│       ├── plugin_helper.py    # --helper 模式入口
│       └── limiter.py          # 移植: 核心限速逻辑
├── config.json
├── requirements.txt
└── MyToolBox.spec
```

### 托盘菜单层级

```
MyToolBox
═══════════
▶ 系统工具
  ✓ 输入状态通知     ← InputStateNotifier 注册的 toggle
──────
▶ 性能工具
  ✓ SGuard 限速      ← SGuardLimiter 注册的 toggle
    状态: 运行中     ← IPC 实时状态
──────
  设置               ← 提示 "编辑 config.json"
  开机自启           ← 通用
═══════════
  退出
```

- 每个 Plugin 的 `category` 决定它归入哪个分组
- toggle 点击时：若插件 disabled → 触发 start；若 enabled → 触发 stop
- Admin 插件的 toggle 会在 start 时触发 UAC

### 插件基类接口

```python
class Plugin:
    name: str                      # "sguard_limiter"
    display_name: str              # "SGuard 限速"
    description: str
    category: str                  # "系统工具" / "性能工具"
    needs_admin: bool
    default_config: dict

    def on_load(self, config: dict): ...
    def on_start(self): ...
    def on_stop(self): ...
    def on_config_change(self, new_config: dict): ...
    def get_menu_items(self) -> list[TrayMenuItem]: ...
```

### UAC 提权流程

```
用户点击 admin 插件的 toggle → enable=True
  ↓
PluginManager.start_plugin("sguard_limiter")
  ↓
检测到 needs_admin=True
  ↓
IpcServer 开始监听 \\.\pipe\MyToolBox\sguard_limiter
  ↓
ShellExecuteW("runas", "MyToolBox.exe --helper sguard_limiter")
  ↓  UAC 弹窗
Helper 进程启动（管理员）
  ↓
连接 Named Pipe → 发送 handshake
  ↓
Core 收到 handshake → 更新菜单状态
  ↓
（持续运行）Helper 定期发送 status → Core 更新菜单
  ↓
用户点击 toggle → disable
  ↓
Core 发 {"action": "stop"} → Helper 收到后退出
```

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| Admin Helper 进程崩溃后菜单显示"运行中"但实际已死 | Helper 发送心跳消息（每 5 秒），Core 监控超时（15 秒无心跳视为死亡） |
| Named Pipe 路径冲突（同机器多实例） | Pipe 路径包含 PID：`\\.\pipe\MyToolBox\sguard_limiter_{core_pid}` |
| UAC 弹窗被用户拒绝后插件处于"启动中"假死 | 设置超时（10 秒），超时后回滚为 disabled 状态 |
| config.json 被用户写坏（格式错误） | ConfigManager 读取时捕获 JSONDecodeError，回退到最后有效配置并记录错误日志 |
| 插件导入失败导致整个应用崩溃 | PluginManager 用 try/except 包裹每个插件的导入，失败则跳过并记录日志 |
| 双进程同时写 config.json | 仅 Core 进程读写 config.json；Helper 通过 IPC 发送配置修改请求 |
