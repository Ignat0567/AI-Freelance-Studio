# Sandbox Test Lab UI

## Using Test Lab

Open **Sandbox Test Lab** from the Studio sidebar. The page checks the protected local backend before enabling either supported operation:

- **Production Self-Test** validates the packaged Studio workflow in the controlled Sandbox environment.
- **Production Screenshot** runs the supported screenshot-oriented Sandbox validation workflow.

Review the confirmation before launch. Windows Sandbox may open and a run can take several minutes. Studio controls only the session created for that run and does not control unrelated Sandbox sessions.

## Availability

Test Lab works only when Studio owns a loopback-only backend and Windows Sandbox capability and the Test Lab job service are ready. It is disabled in network-bind mode. The page explains stable availability reasons but does not change backend settings or expose host paths and security details.

## Run Status

Public statuses are **Queued**, **Preparing**, **Launching**, **Running**, **Cancelling**, **Succeeded**, **Failed**, **Cancelled**, and **Infrastructure error**. Times and duration shown by this first UI are local observation times because the current API does not publish timestamps.

Cancellation is cooperative. A cancellation request can remain in **Cancelling** until the operation reaches a safe interruption point. If the run finishes at the same time, the backend terminal result wins.

## Recovery And Limits

A temporary transport failure shows a reconnecting state and uses bounded status retries. Studio never relaunches a run during recovery. If a restarted backend no longer recognizes the current run, the page explains that tracking is unavailable and returns to selection only after user action.

The current run exists only in renderer memory for the active Studio lifecycle. There is no run-history API or browser persistence. Evidence browsing and downloads are not available, and the page has no command, PowerShell, executable, path, argument, environment, upload or general file controls.

Electron main owns authorization and exposes only four narrow Test Lab operations to the page. The backend token never enters renderer state, storage, URLs, logs or UI diagnostics.
