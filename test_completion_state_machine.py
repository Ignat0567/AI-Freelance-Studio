import main
from pipeline_stage_metadata import PIPELINE_UI_STAGE_ORDER
from test_security_support import authorized_test_client


def test_backend_pipeline_metadata_is_source_of_truth_for_stages_and_agents():
    client = authorized_test_client(main.app)
    metadata = client.get("/api/pipeline/metadata").json()
    agents = main.get_agents()

    assert metadata["stage_order"] == PIPELINE_UI_STAGE_ORDER
    assert metadata["stages"]["product_judge"]["label"] == "Product Judge"
    assert metadata["agent_stages"]["codex"]["stage"] == "coding"
    assert agents["codex"]["stage"] == "coding"
    assert agents["codex"]["display_role"] == "Software Architect"
    assert agents["product_judge"]["role"] == "product_judge"
