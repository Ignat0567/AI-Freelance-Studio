import os
import time

def run_qa_docker_test(project_path: str, project_id: str) -> dict:
    """
    Builds a secure temporary image and evaluates startup container logs.
    Bypasses host pollution and returns an evaluation summary dictionary.
    """
    print(f"[BugCatcher QA]: Initializing container sandbox checks for {project_id}...")
    image_tag = f"studio-sandbox-{project_id.lower()}"
    
    try:
        import docker
    except ImportError:
        return {"success": False, "logs": "Docker Python package not installed. QA sandbox unavailable."}
    
    try:
        client = docker.from_env()
    except Exception as env_err:
        return {"success": False, "logs": f"Docker engine is not active or accessible: {env_err}"}
        
    try:
        # 1. Compile localized workspace docker files into an isolated image
        print(f"[BugCatcher QA]: Compiling image layer tag: {image_tag}...")
        image, build_logs = client.images.build(
            path=project_path,
            tag=image_tag,
            rm=True
        )
        
        # 2. Spin up container detachment runtime inside native bridge isolation network
        print("[BugCatcher QA]: Spinning up test container nodes...")
        container = client.containers.run(
            image=image_tag,
            detach=True,
            network_mode="bridge"
        )
        
        # 3. Allow execution cycle context buffer padding to spin up web endpoints safely
        time.sleep(4)
        container.reload()
        
        # Extract operational log data from the standard output streams
        container_logs = container.logs().decode("utf-8")
        container_status = container.status
        
        print(f"[BugCatcher QA]: Completed test runtime evaluate. State: {container_status}")
        
        # 4. Clean up allocated sandbox footprints immediately to prevent socket leakages
        container.stop()
        container.remove()
        client.images.remove(image=image_tag, force=True)
        
        # If the container runs smoothly or logs don't indicate tracebacks, treat as active
        if container_status in ["running", "exited"]:
            if "error" in container_logs.lower() or "traceback" in container_logs.lower():
                return {"success": False, "logs": container_logs}
            return {"success": True, "logs": container_logs}
        else:
            return {"success": False, "logs": f"Container crashed with exit status code: {container_status}\nLogs:\n{container_logs}"}
            
    except Exception as runtime_fault:
        return {"success": False, "logs": f"Sandbox execution tracking fault: {runtime_fault}"}
