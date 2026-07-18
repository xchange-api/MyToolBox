## ADDED Requirements

### Requirement: Plugin discovery
The system SHALL auto-discover plugins by scanning the `plugins/` directory at startup. Each subdirectory containing an `__init__.py` with a `PLUGIN` attribute exporting a `Plugin` subclass SHALL be recognized as a plugin.

#### Scenario: Discover installed plugins
- **WHEN** MyToolBox starts
- **THEN** the system scans all subdirectories in `plugins/` and loads those with a valid `PLUGIN` entry point

#### Scenario: Skip invalid plugins
- **WHEN** a directory under `plugins/` lacks a valid `PLUGIN` attribute or raises ImportError
- **THEN** the system logs a warning and continues loading remaining plugins

### Requirement: Plugin lifecycle
The system SHALL manage each plugin through a lifecycle: `loaded` → `started` → `stopped`. The `on_load()` method SHALL be called at startup, `on_start()` when the user enables the plugin, and `on_stop()` when the user disables it.

#### Scenario: Plugin starts successfully
- **WHEN** the user enables a plugin via the tray menu
- **THEN** the system calls `plugin.on_start()`
- **THEN** the menu item updates to show the plugin as enabled

#### Scenario: Plugin stops
- **WHEN** the user disables a plugin via the tray menu
- **THEN** the system calls `plugin.on_stop()`
- **THEN** the menu item updates to show the plugin as disabled

### Requirement: Plugin error isolation
A plugin failure SHALL NOT crash the host process. The PluginManager SHALL wrap each plugin call in try/except and log errors.

#### Scenario: Plugin start failure
- **WHEN** `plugin.on_start()` raises an exception
- **THEN** the system logs the error
- **THEN** the plugin remains in "stopped" state
- **THEN** other plugins continue running unaffected

### Requirement: Plugin menu registration
Each plugin SHALL contribute menu items via `get_menu_items()`. The PluginManager SHALL collect all menu items and pass them to the TrayManager for unified menu construction.

#### Scenario: Menu items registration
- **WHEN** a plugin defines `get_menu_items()` returning menu items
- **THEN** those items appear under their `category` group in the tray menu

### Requirement: Plugin configuration
Each plugin SHALL declare `default_config` and `config_schema()`. The ConfigManager SHALL merge per-plugin config from `config.json` with defaults.

#### Scenario: Config merge on load
- **WHEN** a plugin is loaded
- **THEN** the system merges `config.json` values for that plugin with `plugin.default_config`
- **THEN** the plugin receives the merged config via `on_load(merged_config)`
