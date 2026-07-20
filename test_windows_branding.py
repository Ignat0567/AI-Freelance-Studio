import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
PACKAGE = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
ICON_SIZES = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
CERTIFICATE_SUFFIXES = {".pfx", ".p12", ".key", ".cer", ".crt"}


def test_windows_icon_contains_required_transparent_sizes():
    icon_path = FRONTEND / "build" / "icon.ico"
    with Image.open(icon_path) as icon:
        sizes = set(icon.ico.sizes())
        assert ICON_SIZES <= sizes
        for size in ICON_SIZES:
            rgba = icon.ico.getimage(size).convert("RGBA")
            assert rgba.getchannel("A").getextrema()[0] == 0
            assert rgba.getbbox() is not None


def test_transparent_brand_source_has_no_opaque_checkerboard():
    with Image.open(FRONTEND / "build" / "branding" / "icon-source-transparent.png") as image:
        assert image.mode == "RGBA"
        assert image.size == (1024, 1024)
        assert image.getchannel("A").getextrema() == (0, 255)
        assert image.getpixel((0, 0))[3] == 0


def test_windows_branding_and_product_metadata_are_explicit():
    build = PACKAGE["build"]
    win = build["win"]
    nsis = build["nsis"]

    assert PACKAGE["version"] == "1.0.0-beta.1"
    assert PACKAGE["author"] == "AI Labs"
    assert build["appId"] == "com.ailabs.freelancestudio"
    assert build["productName"] == "AI Freelance Studio"
    assert win["executableName"] == "AI Freelance Studio"
    assert win["icon"] == "build/icon.ico"
    assert win["signAndEditExecutable"] is True
    assert nsis["installerIcon"] == "build/icon.ico"
    assert nsis["uninstallerIcon"] == "build/icon.ico"
    assert nsis["shortcutName"] == "AI Freelance Studio"
    assert nsis["createDesktopShortcut"] is True
    assert nsis["uninstallDisplayName"] == "AI Freelance Studio ${version}"
    assert "build/icon.ico" in build["files"]


def test_window_icon_uses_packaged_allow_list_resource():
    main = (FRONTEND / "main.js").read_text(encoding="utf-8")
    assert "icon: path.join(__dirname, 'build', 'icon.ico')" in main


def _signing_validation(env_updates):
    environment = os.environ.copy()
    for name in ("WIN_CSC_LINK", "WIN_CSC_KEY_PASSWORD", "CSC_LINK", "CSC_KEY_PASSWORD"):
        environment.pop(name, None)
    environment.update(env_updates)
    return subprocess.run(
        ["node", "scripts/validate-windows-signing.js"],
        cwd=FRONTEND,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_unsigned_build_validation_succeeds_without_secrets():
    result = _signing_validation({})
    assert result.returncode == 0
    assert "building unsigned artifacts" in result.stdout


def test_complete_signing_configuration_is_accepted_without_logging_secrets():
    link = "private-certificate-path"
    password = "private-certificate-password"
    result = _signing_validation({"WIN_CSC_LINK": link, "WIN_CSC_KEY_PASSWORD": password})
    assert result.returncode == 0
    assert "signing is enabled" in result.stdout
    assert link not in result.stdout + result.stderr
    assert password not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "environment",
    [
        {"WIN_CSC_LINK": "private-certificate-path"},
        {"WIN_CSC_KEY_PASSWORD": "private-password"},
        {"CSC_LINK": "private-certificate-path"},
        {"CSC_KEY_PASSWORD": "private-password"},
    ],
)
def test_partial_signing_configuration_is_rejected_without_logging_secret(environment):
    secret = next(iter(environment.values()))
    result = _signing_validation(environment)
    assert result.returncode == 1
    assert "configuration is incomplete" in result.stderr
    assert secret not in result.stdout + result.stderr


def test_private_certificate_files_are_absent_and_denied():
    assert not (FRONTEND / "build" / "dev_license.pfx").exists()
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    package_patterns = "\n".join(PACKAGE["build"]["files"])
    for suffix in ("*.pfx", "*.p12", "*.pem", "*.key", "*.cer", "*.crt"):
        assert suffix in gitignore
    assert "pfx,p12,pem,key,cer,crt" in package_patterns
    assert not any(
        path.suffix.lower() in CERTIFICATE_SUFFIXES
        for path in (FRONTEND / "build").rglob("*")
        if path.is_file()
    )
    tracked = subprocess.run(
        ["git", "ls-files", "*.pfx", "*.p12", "*.pem", "*.key", "*.cer", "*.crt"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert not any((ROOT / path).exists() for path in tracked)
    for directory in (ROOT / "backups", ROOT / "artifacts", FRONTEND / "build"):
        assert not any(
            path.suffix.lower() in CERTIFICATE_SUFFIXES | {".pem"}
            for path in directory.rglob("*")
            if path.is_file()
        )


def test_backend_trust_store_is_renamed_and_runtime_hook_is_configured():
    builder = (ROOT / "scripts" / "build_backend.py").read_text(encoding="utf-8")
    hook = (ROOT / "scripts" / "pyinstaller_certifi_bundle.py").read_text(encoding="utf-8")
    main = (FRONTEND / "main.js").read_text(encoding="utf-8")
    assert 'pem_bundle.rename(certifi_directory / "cacert.bundle")' in builder
    assert "certifi.where = where" in hook
    assert "REQUESTS_CA_BUNDLE" in main
    assert "SSL_CERT_FILE" in main


def test_smartscreen_limitation_and_signing_environment_are_documented():
    notes = (ROOT / "INTERNAL_BETA_RELEASE.md").read_text(encoding="utf-8")
    assert "binaries are currently unsigned" in notes
    assert "Windows SmartScreen may display a warning" in notes
    assert "Verify the published installer SHA-256" in notes
    assert "Production code signing is planned" in notes
    assert "WIN_CSC_LINK" in notes
    assert "WIN_CSC_KEY_PASSWORD" in notes


def test_installer_checksum_writer_uses_package_version():
    writer = (FRONTEND / "scripts" / "write-installer-sha256.js").read_text(encoding="utf-8")
    assert "packageMetadata.version" in writer
    assert "sha256" in writer.lower()


def _extract_associated_icon(executable: Path, output: Path) -> Image.Image:
    script = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$icon=[System.Drawing.Icon]::ExtractAssociatedIcon('{executable}'); "
        "if ($null -eq $icon) { exit 2 }; "
        "$bitmap=$icon.ToBitmap(); "
        f"$bitmap.Save('{output}', [System.Drawing.Imaging.ImageFormat]::Png); "
        "$bitmap.Dispose(); $icon.Dispose()"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return Image.open(output).convert("RGBA")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE icon resource test")
def test_packaged_application_and_installer_have_branded_pe_icons(tmp_path):
    app = FRONTEND / "installers" / "win-unpacked" / "AI Freelance Studio.exe"
    installer = FRONTEND / "installers" / f"AI Freelance Studio-Setup-{PACKAGE['version']}-win.exe"
    assert app.is_file()
    assert installer.is_file()

    for index, executable in enumerate((app, installer)):
        image = _extract_associated_icon(executable.resolve(), tmp_path / f"icon-{index}.png")
        colors = list(image.getdata())
        purple_pixels = sum(blue > 100 and red > 50 and blue > green * 1.15 for red, green, blue, alpha in colors if alpha)
        assert purple_pixels > len(colors) * 0.01
