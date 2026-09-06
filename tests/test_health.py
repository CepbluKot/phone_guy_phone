from fastapi.testclient import TestClient

from app.main import app


def test_healthz_reports_service_ready() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_conference_page_denies_microphone_without_changing_main_page() -> None:
    client = TestClient(app)

    main = client.get("/")
    conference = client.get("/conference/")

    assert main.status_code == 200
    assert main.headers["permissions-policy"] == "microphone=(self)"
    assert conference.status_code == 200
    assert conference.headers["permissions-policy"] == "microphone=()"
