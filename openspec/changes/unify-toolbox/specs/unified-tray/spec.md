## ADDED Requirements

### Requirement: Single system tray icon
The system SHALL display a single icon in the Windows notification area. The icon SHALL be a 64x64 RGBA image generated at startup.

#### Scenario: Tray icon appears on startup
- **WHEN** MyToolBox starts
- **THEN** an icon appears in the system notification area

### Requirement: Categorized menu structure
The tray menu SHALL organize plugin menu items under group headers based on the plugin's `category` property. Each category SHALL be separated by a menu separator.

#### Scenario: Menu organization by category
- **WHEN** two plugins have categories "系统工具" and "性能工具"
- **THEN** the tray menu shows two groups separated by a menu separator, with plugin items under their respective group

### Requirement: Dynamic menu update
The tray menu SHALL update in real-time when plugin status changes (enabled/disabled) or when IPC status messages arrive from an admin helper process.

#### Scenario: Menu updates on status change
- **WHEN** an admin helper sends a status update via IPC
- **THEN** the corresponding menu item text updates (e.g., "状态: 运行中")
- **WHEN** the user toggles a plugin
- **THEN** the checkmark state updates immediately

### Requirement: Exit menu item
The tray menu SHALL include an "退出" item that stops all plugins, cleans up resources, and exits the application.

#### Scenario: Graceful exit
- **WHEN** the user clicks "退出"
- **THEN** all plugins receive `on_stop()`
- **THEN** admin helper processes are sent stop commands
- **THEN** the tray icon is removed and the process exits
