## ADDED Requirements

### Requirement: Process detection
The system SHALL periodically scan running processes for `SGuard64.exe` using `CreateToolhelp32Snapshot`. Detection interval SHALL be configurable (default 3 seconds).

#### Scenario: Detect SGuard64 process
- **WHEN** SGuard64.exe starts running
- **THEN** the system detects the new PID within the next scan cycle (≤ 3 seconds)

#### Scenario: Detect process exit
- **WHEN** SGuard64.exe exits
- **THEN** the system removes the PID from the active set within the next scan cycle

### Requirement: Process throttling via Job Object
The system SHALL first attempt to create a Windows Job Object with IDLE priority class and a hard CPU rate cap (default 5% of one core), then assign the target process to this job.

#### Scenario: Job Object assignment
- **WHEN** a SGuard64.exe process is detected
- **THEN** the system attempts to create a Job Object and assign the process to it
- **THEN** logs whether the assignment succeeded or failed

### Requirement: Fallback throttling
If Job Object assignment fails (process already in a job, insufficient permissions), the system SHALL apply direct process-level limits: IDLE priority class, background processing mode, very low I/O priority, and CPU affinity to the last core.

#### Scenario: Fallback to process limits
- **WHEN** Job Object assignment fails
- **THEN** the system applies `SetPriorityClass(IDLE_PRIORITY_CLASS)`
- **THEN** the system applies `SetProcessAffinityMask` (pin to last core)
- **THEN** applies `PROCESS_MODE_BACKGROUND_BEGIN` and very low I/O priority

### Requirement: Thread-level throttling
The system SHALL enumerate all threads of the target process and set each thread to `THREAD_PRIORITY_IDLE` pinned to the last CPU core.

#### Scenario: Throttle all threads
- **WHEN** Thread-level throttling runs
- **THEN** every thread of the target process gets IDLE priority and last-core affinity
- **THEN** logs the count of throttled threads (e.g., "threads_31/31")

### Requirement: Periodic re-application
Thread-level limits SHALL be re-applied every 30 seconds to counter anti-throttling mechanisms that may reset thread priorities. Process-level limits SHALL be re-evaluated every scan cycle.

#### Scenario: Re-apply limits
- **WHEN** 30 seconds have elapsed since last application
- **THEN** the system re-applies thread-level limits to all known PIDs

### Requirement: Admin-only execution
This plugin SHALL require administrator privileges. The `needs_admin` property SHALL be `True`, causing the plugin system to launch it as an out-of-process Admin Helper.

#### Scenario: Elevation on enable
- **WHEN** the user enables SGuard Limiter
- **THEN** the system requests elevation via UAC
- **THEN** the limiter logic runs in an admin helper process with full privileges
