# AI Freelancer Studio Internal Beta 1.0.0-beta.1

Recommended git tag: `v1.0.0-beta.1`

## Changelog

- Added a Windows installer with a bundled backend that does not require Python.
- Moved writable runtime data to the Electron user-data directory.
- Added backend health readiness, process-tree cleanup, and user-visible startup errors.
- Added package sensitive-path checks and focused Electron launcher coverage.
- Added the official AI Freelancer Studio icon to Windows executables, installer, uninstaller, shortcuts, taskbar, and Apps & Features metadata.

## Known Limitations

- Windows binaries are currently unsigned. Windows SmartScreen may display a warning.
- Verify the published installer SHA-256 before running it. Production code signing is planned for a later release.
- A clean-machine install/uninstall run has not yet been performed on an isolated Windows system.
- One real OpenCode small-project run still requires validation with an authorized provider account.

## Release Checklist

- [ ] Confirm the final installer is `frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe`.
- [ ] Verify the installer checksum and archive it with this release note.
- [ ] Complete the clean-machine checklist below.
- [ ] Complete the live OpenCode checklist below with a non-sensitive test project.
- [ ] Review `%APPDATA%\AI Freelance Studio\backend-startup.log` only if a startup failure occurs.
- [ ] Create tag `v1.0.0-beta.1` after approval. Do not create it before approval.

## Future Windows Code Signing

Signing is opt-in and uses the standard electron-builder signing flow. Keep all values in local environment variables or a CI secret store; never add certificate files or passwords to the repository, backups, logs, ASAR, or installer resources.

- Preferred Windows variables: `WIN_CSC_LINK` and `WIN_CSC_KEY_PASSWORD`.
- Generic electron-builder variables: `CSC_LINK` and `CSC_KEY_PASSWORD`.
- Set one complete pair only. A partial pair fails before packaging.
- With no signing variables, `npm run package:win` produces the supported unsigned local build.

## Clean-Machine Checklist

- [ ] Use a Windows system without Python, Node.js, or a source checkout.
- [ ] Install the Windows installer as a standard user.
- [ ] Launch from the Start Menu and confirm the main window opens.
- [ ] Confirm the backend becomes healthy and no Python installation is requested.
- [ ] Create a small local test project, close the application, and confirm no backend process remains.
- [ ] Relaunch and confirm project and settings state restore from Electron user data.
- [ ] Uninstall without requesting data deletion and confirm user data remains.
- [ ] Reinstall and confirm preserved state restores.

## Live OpenCode Small-Project Checklist

- [ ] Use a dedicated authorized provider account with a spend limit.
- [ ] Configure and test the OpenCode/provider connection in Settings.
- [ ] Create a small, non-sensitive project with clear acceptance criteria.
- [ ] Run the pipeline and confirm generated files are present in the project directory.
- [ ] Run QA, introduce one safe artificial defect, and confirm repair reruns QA.
- [ ] Run delivery audit and open the generated result.
- [ ] Confirm credentials do not appear in project files, application logs, or reports.
- [ ] Record outcome, provider/model, and any user-visible errors in the internal beta tracker.
