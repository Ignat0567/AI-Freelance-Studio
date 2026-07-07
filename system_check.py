import shutil
import subprocess
import sys


def check_and_install_dependencies():
    """
    Checks for the presence of Docker and Ollama in the system path.
    Prompts the user or attempts automatic setup if missing.
    Complies fully with standard PEP 8.
    """
    print("[SYSTEM CHECK] Verifying core infrastructure components...")

    # 1. Validate Docker installation
    docker_path = shutil.which("docker")
    if not docker_path:
        print("⚠️ [WARNING]: Docker is not found in your system PATH.")
        print("Docker is required for safe isolated testing of freelance tasks.")
        setup_docker()
    else:
        print(f"✅ [OK]: Docker detected at {docker_path}")

    # 2. Validate Ollama installation (for free local models)
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        print("⚠️ [WARNING]: Ollama is not found in your system PATH.")
        print("Ollama is recommended for running 100% free offline AI models.")
        setup_ollama()
    else:
        print(f"✅ [OK]: Ollama detected at {ollama_path}")


def setup_docker():
    """
    Provides installation logic or guidelines depending on the OS platform.
    """
    platform = sys.platform
    print(f"[INSTALLER]: Detected operating system platform: {platform}")

    if platform == "linux":
        try:
            print("[INSTALLER]: Attempting automated Docker setup via apt...")
            # Run package updates and installation non-interactively
            subprocess.run(["sudo", "apt-get", "update", "-y"], check=True)
            subprocess.run(
                ["sudo", "apt-get", "install", "docker.io", "-y"], check=True
            )
            print("✅ [SUCCESS]: Docker engine installed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"❌ [ERROR]: Automated linux setup failed: {e}")
            print("Please run: 'sudo apt install docker.io' manually.")
    elif platform in ["win32", "darwin"]:
        # Desktop systems require Docker Desktop installer due to virtualization
        print("[INSTALLER]: Manual action required for Desktop environments.")
        url = "https://docker.com"
        print(f"Please install Docker Desktop from the official page: {url}")


def setup_ollama():
    """
    Handles automatic download execution for Ollama framework where supported.
    """
    platform = sys.platform
    if platform == "linux":
        try:
            print("[INSTALLER]: Executing official Ollama installation script...")
            # Pipe curl download directly into sh execution safely
            cmd = "curl -fsSL https://ollama.com | sh"
            subprocess.run(cmd, shell=True, check=True)
            print("✅ [SUCCESS]: Ollama service installed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"❌ [ERROR]: Ollama automated script failed: {e}")
    elif platform == "darwin":
        print("[INSTALLER]: macOS detected. Download Ollama application.")
        print("Link: https://ollama.com")
    elif platform == "win32":
        print("[INSTALLER]: Windows detected. Run official executable installer.")
        print("Link: https://ollama.com")


if __name__ == "__main__":
    check_and_install_dependencies()
