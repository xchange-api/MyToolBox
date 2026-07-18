## ADDED Requirements

### Requirement: Named Pipe transport
The IPC system SHALL use Windows Named Pipes for communication between the Core process and Admin Helper processes. The pipe path SHALL be `\\.\pipe\MyToolBox\{plugin_name}_{core_pid}`.

#### Scenario: Pipe creation
- **WHEN** the Core starts an admin plugin
- **THEN** the Core creates a Named Pipe server instance before launching the helper process

#### Scenario: Helper connects
- **WHEN** the helper process starts
- **THEN** it connects to the Named Pipe using the plugin name and Core PID (passed via command line)
- **THEN** it sends a `handshake` message

### Requirement: JSON-line protocol
All IPC messages SHALL be single-line JSON objects terminated by a newline character. Each message SHALL have a `type` field.

#### Scenario: Handshake
- **WHEN** the helper connects
- **THEN** it sends `{"type": "handshake", "pid": <helper_pid>, "plugin": "<plugin_name>"}`
- **THEN** the Core responds with `{"type": "handshake_ack"}`

#### Scenario: Status reporting
- **WHEN** the helper detects a state change
- **THEN** it sends a status message to the Core, e.g., `{"type": "status", "running": true, "pids": [1234]}`

#### Scenario: Stop command
- **WHEN** the user disables an admin plugin
- **THEN** the Core sends `{"type": "command", "action": "stop"}` to the helper
- **THEN** the helper calls its `on_stop()` and exits

### Requirement: Heartbeat monitoring
The Admin Helper SHALL send a heartbeat message every 5 seconds. The Core SHALL consider the helper dead if no message is received for 15 seconds.

#### Scenario: Helper crash detection
- **WHEN** the Core receives no IPC message for 15 seconds from a helper
- **THEN** the Core marks the plugin as stopped
- **THEN** the menu updates to reflect the stopped state
