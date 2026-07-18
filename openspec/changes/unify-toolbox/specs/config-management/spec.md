## ADDED Requirements

### Requirement: Config file format
The system SHALL read configuration from `config.json` in the application root directory. The file SHALL use UTF-8 encoding and standard JSON format.

#### Scenario: Read config on startup
- **WHEN** MyToolBox starts and `config.json` exists
- **THEN** the system reads and parses all settings from the file
- **WHEN** `config.json` does not exist
- **THEN** the system creates the file with default values based on all loaded plugins

### Requirement: Default value merge
The ConfigManager SHALL merge plugin `default_config` values with user settings in `config.json`. User-provided values SHALL override defaults. Plugin config section keys SHALL match the plugin's `name` property.

#### Scenario: Merge user config with defaults
- **WHEN** `config.json` has `"sguard_limiter": {"cpu_percent": 10}`
- **AND** the plugin's `default_config` has `{"cpu_percent": 5, "monitor_interval": 3.0}`
- **THEN** the merged config for the plugin is `{"cpu_percent": 10, "monitor_interval": 3.0}`

### Requirement: Config error resilience
The system SHALL handle invalid config.json gracefully. On JSON parse error, the system SHALL fall back to default config for all plugins and log the error.

#### Scenario: Corrupted config file
- **WHEN** `config.json` contains invalid JSON
- **THEN** the system logs a parse error
- **THEN** all plugins receive their default configuration
- **THEN** the system continues normal operation

### Requirement: Config structure
The config.json SHALL have a top-level `general` section for app-wide settings and a `plugins` section with per-plugin settings.

#### Scenario: Config file structure
- **WHEN** a valid config.json is loaded
- **THEN** `general.autostart` controls startup shortcut (boolean)
- **THEN** `general.log_level` controls log verbosity ("debug", "info", "warning", "error")
- **THEN** each key under `plugins` maps to a plugin name with its settings
