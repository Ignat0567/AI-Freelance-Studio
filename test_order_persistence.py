from pathlib import Path

from order_workflow.api_models import CreateOrderRequest
from order_workflow.order_store import InMemoryOrderStore, JsonExecutionStore, JsonOrderStore
from order_workflow.service import OrderWorkflowService


def _payload(title="Persisted order"):
    return CreateOrderRequest(title=title, description="D" * 40, product_type="web_app", preferred_language="en", constraints=(), attachments=())


def test_order_survives_a_simulated_backend_restart(tmp_path):
    store = JsonOrderStore(tmp_path / "orders_state.json")
    exec_store = JsonExecutionStore(tmp_path / "executions_state.json")

    first = OrderWorkflowService(store=store, execution_state_store=exec_store)
    snapshot = first.create_order(_payload())
    order_id = snapshot["order"]["id"]

    second = OrderWorkflowService(store=store, execution_state_store=exec_store)
    restored = second.snapshot(order_id)

    assert restored["order"]["id"] == order_id
    assert restored["order"]["title"] == "Persisted order"


def test_list_orders_reports_newest_first_with_summary_fields(tmp_path):
    store = JsonOrderStore(tmp_path / "orders_state.json")
    service = OrderWorkflowService(store=store)
    first = service.create_order(_payload("First"))["order"]["id"]
    second = service.create_order(_payload("Second"))["order"]["id"]

    rows = service.list_orders()

    assert [row["id"] for row in rows] == [second, first]
    assert rows[0]["title"] == "Second"
    assert rows[0]["status"] == "clarification_required"
    assert rows[0]["execution_status"] is None


def test_json_order_store_survives_a_missing_file(tmp_path):
    store = JsonOrderStore(tmp_path / "does_not_exist" / "orders_state.json")

    assert store.load() == {}


def test_json_order_store_tolerates_corrupt_json(tmp_path):
    path = tmp_path / "orders_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = JsonOrderStore(path)

    assert store.load() == {}


def test_operator_api_persists_orders_to_json_not_memory():
    source = Path("api/orders.py").read_text(encoding="utf-8")
    assert "JsonOrderStore" in source
    assert "orders_state.json" in source
    assert "JsonExecutionStore" in source


def test_in_memory_order_store_is_the_default_and_does_not_touch_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = OrderWorkflowService()
    service.create_order(_payload())

    assert list(tmp_path.iterdir()) == []
    assert isinstance(service._store, InMemoryOrderStore)


def test_json_execution_store_persists_a_started_execution(tmp_path):
    from order_workflow.models import ExecutionStage, ExecutionStatus, ProjectExecution
    from datetime import datetime, timezone

    path = tmp_path / "executions_state.json"
    store = JsonExecutionStore(path)
    execution = ProjectExecution(
        id="execution_abc",
        order_id="order_abc",
        brief_id="brief_abc",
        handoff_id="handoff_abc",
        mode="fake",
        status=ExecutionStatus.QUEUED,
        stage=ExecutionStage.REQUIREMENTS,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    store.save(execution)
    loaded = store.load()

    assert len(loaded) == 1
    assert loaded[0].id == "execution_abc"
    assert path.is_file()
