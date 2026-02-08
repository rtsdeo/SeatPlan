from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_core_flow():
    r = client.post(
        "/exam-sessions",
        json={
            "name": "FN Session",
            "exam_date": "2026-05-10",
            "start_time": "09:00",
            "end_time": "12:00",
            "subject_code": "MAT101",
            "subject_name": "Math",
        },
    )
    assert r.status_code == 200
    session_id = r.json()["id"]

    r = client.post(
        "/students",
        json={
            "registration_no": "22CS001",
            "name": "Jane",
            "student_type": "regular",
            "subject_code": "MAT10I",  # fuzzy near MAT101
        },
    )
    assert r.status_code == 200
    assert r.json()["matched_exam_session_id"] == session_id

    r = client.post(
        "/rooms",
        json={"name": "A-101", "columns": [5, 5], "seat_capacity": 2},
    )
    assert r.status_code == 200
    room_id = r.json()["id"]
    assert r.json()["capacity"] == 20

    r = client.get("/subject-pool", params={"exam_date": "2026-05-10"})
    assert r.status_code == 200
    assert r.json()[0]["student_count"] >= 1

    r = client.post(
        "/seat-plan",
        json={
            "exam_date": "2026-05-10",
            "exam_session_id": session_id,
            "room_id": room_id,
            "auto_generated": True,
        },
    )
    assert r.status_code == 200
    plan_id = r.json()["id"]

    r = client.get(f"/seat-plan/{plan_id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
