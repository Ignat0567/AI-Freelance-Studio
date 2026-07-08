import importlib

from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch):
    import main

    importlib.reload(main)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test-tickets.db")
    main.init_db()
    return TestClient(main.app)


def create_ticket(client, **overrides):
    payload = {
        "client_name": "Иван Петров",
        "contact": "ivan@example.com",
        "company": "North IT",
        "description": "Не запускается рабочая станция",
        "priority": "обычный",
        "status": "новая",
    }
    payload.update(overrides)
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_ticket_appears_in_list_and_stats(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    ticket = create_ticket(client, priority="срочный")

    list_response = client.get("/api/tickets")
    assert list_response.status_code == 200
    tickets = list_response.json()
    assert len(tickets) == 1
    assert tickets[0]["id"] == ticket["id"]
    assert tickets[0]["client_name"] == "Иван Петров"

    stats = client.get("/api/stats").json()
    assert stats == {"new": 1, "in_progress": 0, "urgent": 1, "closed_today": 0}


def test_update_status_add_comment_search_and_filter(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    ticket = create_ticket(client)

    update_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "в работе", "priority": "высокий"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "в работе"

    comment_response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"author": "Администратор", "text": "Клиент ждет звонка"},
    )
    assert comment_response.status_code == 201

    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["comments"][0]["text"] == "Клиент ждет звонка"

    filtered = client.get("/api/tickets", params={"status": "в работе", "priority": "высокий"}).json()
    assert [item["id"] for item in filtered] == [ticket["id"]]

    searched = client.get("/api/tickets", params={"search": "ждет звонка"}).json()
    assert [item["id"] for item in searched] == [ticket["id"]]

    stats = client.get("/api/stats").json()
    assert stats["new"] == 0
    assert stats["in_progress"] == 1


def test_closed_today_and_delete_ticket(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    ticket = create_ticket(client)

    close_response = client.put(f"/api/tickets/{ticket['id']}", json={"status": "закрыта"})
    assert close_response.status_code == 200
    assert close_response.json()["closed_at"] is not None
    assert client.get("/api/stats").json()["closed_today"] == 1

    delete_response = client.delete(f"/api/tickets/{ticket['id']}")
    assert delete_response.status_code == 204
    assert client.get("/api/tickets").json() == []
    assert client.get(f"/api/tickets/{ticket['id']}").status_code == 404


def test_database_persists_between_clients(tmp_path, monkeypatch):
    first_client = make_client(tmp_path, monkeypatch)
    ticket = create_ticket(first_client, contact="persist@example.com")

    second_client = make_client(tmp_path, monkeypatch)
    response = second_client.get(f"/api/tickets/{ticket['id']}")

    assert response.status_code == 200
    assert response.json()["contact"] == "persist@example.com"


def test_home_page_is_served(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert "Nerva Desk" in response.text
    assert "Новая заявка" in response.text


def test_security_headers_are_sent(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_search_query_length_is_limited(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/api/tickets", params={"search": "x" * 201})

    assert response.status_code == 422


def test_required_fields_cannot_be_whitespace_only(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/tickets",
        json={
            "client_name": "   ",
            "contact": "ivan@example.com",
            "description": "Не запускается рабочая станция",
        },
    )

    assert response.status_code == 422


def test_comment_text_cannot_be_whitespace_only(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    ticket = create_ticket(client)

    response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"author": "Администратор", "text": "   "},
    )

    assert response.status_code == 422
