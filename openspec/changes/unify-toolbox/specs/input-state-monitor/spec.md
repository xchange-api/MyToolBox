## ADDED Requirements

### Requirement: CapsLock state monitoring
The system SHALL detect CapsLock state changes and display a toast notification. When CapsLock turns ON, show "ABC"; when OFF, show "abc".

#### Scenario: CapsLock toggles ON
- **WHEN** the user presses the CapsLock key
- **THEN** the system detects CapsLock is now ON
- **THEN** a toast notification with "ABC" appears on screen for 0.8 seconds

#### Scenario: CapsLock toggles OFF
- **WHEN** the user presses the CapsLock key
- **THEN** the system detects CapsLock is now OFF
- **THEN** a toast notification with "abc" appears on screen for 0.8 seconds

### Requirement: NumLock state monitoring
The system SHALL detect NumLock state changes. When NumLock is ON, show "123"; when OFF, show "123" with strikethrough text.

#### Scenario: NumLock toggles
- **WHEN** the user presses the NumLock key
- **THEN** a toast notification indicates the new state (123 with or without strikethrough)

### Requirement: IME state monitoring
The system SHALL detect IME Chinese/English mode switches triggered by Shift key press or Ctrl+Space. When Chinese IME is active, show "中"; when English IME is active, show "ABC" or "abc" based on CapsLock state.

#### Scenario: IME switches to Chinese
- **WHEN** the user presses Shift (left or right) or Ctrl+Space to switch IME mode
- **AND** the foreground window's IME is now in Chinese mode
- **THEN** a toast notification with "中" appears

#### Scenario: IME switches to English with CapsLock OFF
- **WHEN** the user switches IME to English mode
- **AND** CapsLock is OFF
- **THEN** a toast notification with "abc" appears

### Requirement: Toast popup display
The toast SHALL be a semi-transparent overlay window centered on the primary monitor's work area, rendered with per-pixel alpha blending, displayed for a configurable duration.

#### Scenario: Toast appearance
- **WHEN** a state change is detected
- **THEN** a 160x80 pixel semi-transparent popup appears centered on screen
- **THEN** the popup auto-hides after the configured duration (default 0.8 seconds)

### Requirement: Debounce
Rapid toggling of the same type SHALL be debounced. If a state change of the same type (caps/num/ime) occurs within 300ms of the previous toast, the second toast SHALL be suppressed.

#### Scenario: Rapid CapsLock toggles
- **WHEN** the user presses CapsLock twice within 300ms
- **THEN** only one toast notification is shown for the final state
