from fastapi.testclient import TestClient

from backend_security import LocalSecurityContext, set_app_security_context


TEST_LOCAL_TOKEN = "test-only-local-token-32-bytes-long"
TEST_LOCAL_ORIGIN = "http://127.0.0.1:8080"


def authorized_test_client(app) -> TestClient:
    set_app_security_context(
        app,
        LocalSecurityContext.create(
            token=TEST_LOCAL_TOKEN,
            bind_host="127.0.0.1",
            port=8080,
            launch_id="00000000-0000-0000-0000-000000000001",
            allow_test_client=True,
        ),
    )
    return TestClient(
        app,
        base_url=TEST_LOCAL_ORIGIN,
        headers={
            "X-FreelancerStudio-Token": TEST_LOCAL_TOKEN,
            "Origin": TEST_LOCAL_ORIGIN,
            "Content-Type": "application/json",
        },
    )
