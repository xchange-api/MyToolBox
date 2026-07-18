## ADDED Requirements

### Requirement: On-demand elevation
The system SHALL NOT request administrator privileges at startup. Elevation SHALL ONLY occur when the user explicitly enables a plugin with `needs_admin=True`.

#### Scenario: No UAC on startup
- **WHEN** MyToolBox starts
- **THEN** no UAC prompt is shown
- **THEN** normal-priority plugins start immediately

#### Scenario: UAC triggered by admin plugin
- **WHEN** the user enables a plugin with `needs_admin=True`
- **THEN** the system creates the Named Pipe server
- **THEN** the system calls `ShellExecuteW("runas")` to launch `MyToolBox.exe --helper <plugin_name> --core-pid <pid>`
- **THEN** a UAC prompt is displayed

### Requirement: Elevation timeout
If the user cancels the UAC prompt or the helper process fails to connect within 10 seconds, the system SHALL revert the plugin to disabled state.

#### Scenario: UAC cancelled
- **WHEN** the user clicks "No" on the UAC prompt
- **THEN** the system times out after 10 seconds
- **THEN** the plugin state reverts to disabled
- **THEN** the menu shows the plugin as disabled

### Requirement: Cleanup on exit
When MyToolBox exits, if any admin helper processes are running, the Core SHALL send a stop command via IPC. If the helper does not respond within 3 seconds, the Core SHALL terminate the process by PID.

#### Scenario: Admin helper cleanup on exit
- **WHEN** the user exits MyToolBox while an admin plugin is running
- **THEN** the Core sends stop command via IPC
- **THEN** waits up to 3 seconds for graceful shutdown
- **THEN** terminates the helper process if still alive
