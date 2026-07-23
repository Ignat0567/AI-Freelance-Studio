from backend_security import LocalSecurityContext, set_app_security_context
from main import app
from test_security_support import TEST_LOCAL_TOKEN


set_app_security_context(
    app,
    LocalSecurityContext.create(
        token=TEST_LOCAL_TOKEN,
        bind_host="127.0.0.1",
        port=8080,
        launch_id="ui-regression-server",
    ),
)
