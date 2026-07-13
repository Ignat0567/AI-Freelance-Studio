import delivery_audit


SOURCE = '''
def test_end_to_end_behavior(client):
    created = client.post("/work-items", json={"title": "x"})
    assert created.status_code == 201
    item = created.json()
    assert item["id"]
    dashboard = client.get("/dashboard")
    assert dashboard.json()["new"] == 1
    found = client.get("/work-items", params={"search": "x", "priority": "high"})
    assert found.json()[0]["id"] == item["id"]
    detail = client.get(f"/work-items/{item['id']}")
    assert detail.json()["id"] == item["id"]
    updated = client.patch(f"/work-items/{item['id']}", json={"status": "closed"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "closed"
    rejected = client.delete(f"/work-items/{item['id']}")
    assert rejected.status_code == 400
    deleted = client.delete(f"/work-items/{item['id']}", params={"confirm": True})
    assert deleted.status_code == 200
    assert client.get(f"/work-items/{item['id']}").status_code == 404
'''


def test_routes_and_assertions_produce_explainable_semantic_coverage():
    result = delivery_audit._ac019_test_coverage(SOURCE, "tests/test_behavior.py")
    matrix = result["coverage_matrix"]
    for category in ("create", "read_list", "update", "delete", "search", "filtering", "dashboard_metrics"):
        assert matrix[category]
        assert matrix[category][0]["test_function"] == "test_end_to_end_behavior"
        assert matrix[category][0]["detection_signal"]
        assert matrix[category][0]["confidence"] == "strong"


def test_same_process_retrieval_does_not_imply_restart_persistence():
    result = delivery_audit._ac019_test_coverage(SOURCE, "tests/test_behavior.py")
    assert result["coverage_matrix"]["restart_persistence"] == []


def test_test_names_are_secondary_non_satisfying_signals():
    result = delivery_audit._ac019_test_coverage("def test_create_ticket():\n    assert True\n", "tests/test_names.py")
    assert result["coverage_matrix"]["create"] == []
    assert result["weak_name_signals"]["create"][0]["confidence"] == "weak"


def test_restart_persistence_requires_lifecycle_and_retrieval_signals():
    source = '''
def test_restart_keeps_record(client):
    client.stop()
    client.start()
    app.reload()
    result = client.get("/work-items/1")
    assert result.status_code == 200
'''
    result = delivery_audit._ac019_test_coverage(source, "tests/test_restart.py")
    assert result["coverage_matrix"]["restart_persistence"]
