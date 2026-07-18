## Why

SGuardLimiter 和 InputStateNotifier 是两个独立的 Windows 系统工具，共享相同的技术栈（pystray/Pillow/pywin32、Win32 API、系统托盘常驻）。当前独立维护两份项目，启动两个托盘图标，配置分散。未来还会添加更多类似的小工具——需要一个统一的平台来承载。

## What Changes

- 创建一个 **MyToolBox** 统一应用，以插件架构承载所有小工具
- 将 SGuardLimiter 的功能封装为 `sguard_limiter` 插件
- 将 InputStateNotifier 的功能封装为 `input_state_notifier` 插件
- 实现插件系统核心基础设施：插件管理器、配置管理器、统一托盘菜单、IPC 通信、日志
- 支持管理员权限隔离：普通插件直接运行，需要提权的插件通过 `--helper` 子进程运行
- 引入 `config.json` 统一配置文件，替代各工具的硬编码常量
- 打包为单个 EXE，支持 `--helper <plugin>` 命令行模式

## Capabilities

### New Capabilities
- `plugin-system`: 插件发现、加载、生命周期管理、菜单注册的标准化接口
- `unified-tray`: 统一系统托盘图标，支持按分组组织的菜单结构
- `config-management`: config.json 读写、校验、默认值合并
- `ipc-transport`: 基于 Named Pipe 的 JSON-line IPC 协议，用于 Core 与 Admin Helper 通信
- `uac-elevation`: 按需提权策略——Core 运行于普通权限，admin helper 进程触发 UAC
- `input-state-monitor`: 从 InputStateNotifier 迁移——CapsLock/NumLock/IME 状态监测 + Toast 弹窗
- `sguard-limiter`: 从 SGuardLimiter 迁移——SGuard64.exe 进程限速

### Modified Capabilities

None.

## Impact

- SGuardLimiter 和 InputStateNotifier 仓库将归档，代码迁移至 MyToolBox
- 新增依赖：无（现有依赖 pystray/Pillow/pywin32 已在两个项目中使用）
- 现有功能行为保持不变，配置方式从硬编码改为 config.json
- 引入进程间通信（Named Pipe）用于 admin helper 模式
