# Device Monitor 1.0

A free Apple Silicon Mac dashboard for local-model experiments and development
servers running in the background.

- CPU/GPU activity, CPU/GPU temperatures, RAM, swap, and fan RPM.
- A five-minute timeline with separate units for activity, temperatures, and fans.
- Fan-rise markers and nearby top-CPU-process snapshots (correlation, not diagnosis).
- Top processes with PIDs, CPU, resident RAM, local TCP ports, filtering and sorting.
- Process details and a project-folder hint where accessible.
- Native menu-bar readings and compact summary; hide/reopen the dashboard.
- Automatic sensor reconnection and a manual retry command.
- No accounts, analytics, network requests, or saved monitoring history.

## Install

Download **Device-Monitor-1.0-Apple-Silicon.dmg**, open it, drag the app to Applications,
and eject the image. Python and runtime dependencies are bundled.

This build is **ad-hoc signed and not Apple notarized**. If first launch is blocked,
try opening once, then go to **System Settings > Privacy & Security > Open Anyway**
and confirm Open, only if you trust this download. Managed Macs may restrict it.
https://support.apple.com/102445

Close/Hide the dashboard to keep monitoring in the menu bar. Use **Quit Device
Monitor / Command-Q** to stop the app and its readers.

## Compatibility and validation

Apple Silicon only. Build target macOS 11+; tested on macOS 26.6.1. Intel and older
macOS versions are not verified. Fanless Macs may have no fan readings, and some
hardware measurements may be unavailable. Monitoring does not need root access.

Nine automated checks cover collection, a real local server/port, timeout and
restart recovery, and fan-event behavior. Source, packaged app, and mounted-DMG
checks cover readings, three window sizes, details/help windows, native menu
show/hide actions, and clean reader shutdown. A browser download/installation on
another Mac was not performed.

The source ZIP and SHA256SUMS.txt accompany this release. MIT license, with
bundled third-party notices and license texts.
