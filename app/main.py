from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pypdf import PdfReader
from rapidfuzz import fuzz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlmodel import Field, Relationship, SQLModel, Session, create_engine, select

DATABASE_URL = "sqlite:///seatplan.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class ExamSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    exam_date: date
    start_time: str
    end_time: str
    subject_code: str
    subject_name: str
    student_count: int = 0


class Student(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    registration_no: str = Field(index=True, unique=True)
    name: str
    student_type: str = Field(default="regular")
    subject_code: str
    matched_exam_session_id: int | None = Field(default=None, foreign_key="examsession.id")


class Room(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    columns_json: str = Field(default="[]")
    seat_capacity: int = 2


class SeatPlan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    exam_date: date
    exam_session_id: int = Field(foreign_key="examsession.id")
    room_id: int = Field(foreign_key="room.id")
    auto_generated: bool = True
    mapping_json: str


class ExamSessionCreate(SQLModel):
    name: str
    exam_date: date
    start_time: str
    end_time: str
    subject_code: str
    subject_name: str
    student_count: int = 0


class StudentCreate(SQLModel):
    registration_no: str
    name: str
    student_type: str = "regular"
    subject_code: str


class RoomCreate(SQLModel):
    name: str
    columns: list[int]
    seat_capacity: int = 2


class SeatPlanCreate(SQLModel):
    exam_date: date
    exam_session_id: int
    room_id: int
    auto_generated: bool = True
    mapping: dict[str, str] | None = None


app = FastAPI(title="Exam Seat Planner")


def get_session():
    with Session(engine) as session:
        yield session


@app.on_event("startup")
def on_startup() -> None:
    SQLModel.metadata.create_all(engine)


def parse_pdf_text(file: UploadFile) -> str:
    reader = PdfReader(io.BytesIO(file.file.read()))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_schedule_from_text(text: str) -> list[dict[str, Any]]:
    # Example expected pattern: "2026-05-10 | FN | MAT101 | Mathematics I"
    matches = re.findall(
        r"(\d{4}-\d{2}-\d{2})\s*\|\s*([A-Za-z0-9:]+)\s*\|\s*([A-Za-z0-9-]+)\s*\|\s*([^\n]+)",
        text,
    )
    items: list[dict[str, Any]] = []
    for dt, slot, code, name in matches:
        items.append(
            {
                "name": f"{slot} Session",
                "exam_date": date.fromisoformat(dt),
                "start_time": "09:00" if slot.upper() == "FN" else "14:00",
                "end_time": "12:00" if slot.upper() == "FN" else "17:00",
                "subject_code": code,
                "subject_name": name.strip(),
            }
        )
    return items


def extract_students_from_text(text: str) -> list[dict[str, str]]:
    # Example pattern: "22CS001, Jane Doe, regular, MAT101"
    matches = re.findall(r"([A-Za-z0-9]+)\s*,\s*([^,]+)\s*,\s*(regular|partial)\s*,\s*([A-Za-z0-9-]+)", text, re.I)
    return [
        {
            "registration_no": reg,
            "name": name.strip(),
            "student_type": stype.lower(),
            "subject_code": code,
        }
        for reg, name, stype, code in matches
    ]


def fuzzy_match_session(subject_code: str, sessions: list[ExamSession]) -> int | None:
    best_id, best_score = None, 0
    for sess in sessions:
        score = fuzz.ratio(subject_code.upper(), sess.subject_code.upper())
        if score > best_score:
            best_id, best_score = sess.id, score
    return best_id if best_score >= 75 else None


def room_capacity(room: Room) -> int:
    cols = json.loads(room.columns_json)
    return sum(cols) * room.seat_capacity


@app.post("/exam-sessions", response_model=ExamSession)
def create_exam_session(payload: ExamSessionCreate, session: Session = Depends(get_session)):
    row = ExamSession.model_validate(payload)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.get("/exam-sessions", response_model=list[ExamSession])
def list_exam_sessions(session: Session = Depends(get_session)):
    return session.exec(select(ExamSession)).all()


@app.put("/exam-sessions/{session_id}", response_model=ExamSession)
def update_exam_session(session_id: int, payload: ExamSessionCreate, session: Session = Depends(get_session)):
    row = session.get(ExamSession, session_id)
    if not row:
        raise HTTPException(404, "Exam session not found")
    update_data = payload.model_dump()
    for key, value in update_data.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/exam-sessions/{session_id}")
def delete_exam_session(session_id: int, session: Session = Depends(get_session)):
    row = session.get(ExamSession, session_id)
    if not row:
        raise HTTPException(404, "Exam session not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


@app.post("/students", response_model=Student)
def create_student(payload: StudentCreate, session: Session = Depends(get_session)):
    sessions = session.exec(select(ExamSession)).all()
    match_id = fuzzy_match_session(payload.subject_code, sessions)
    row = Student.model_validate(payload)
    row.matched_exam_session_id = match_id
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.get("/students", response_model=list[Student])
def list_students(session: Session = Depends(get_session)):
    return session.exec(select(Student)).all()


@app.put("/students/{student_id}", response_model=Student)
def update_student(student_id: int, payload: StudentCreate, session: Session = Depends(get_session)):
    row = session.get(Student, student_id)
    if not row:
        raise HTTPException(404, "Student not found")
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    sessions = session.exec(select(ExamSession)).all()
    row.matched_exam_session_id = fuzzy_match_session(row.subject_code, sessions)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/students/{student_id}")
def delete_student(student_id: int, session: Session = Depends(get_session)):
    row = session.get(Student, student_id)
    if not row:
        raise HTTPException(404, "Student not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


@app.post("/rooms")
def create_room(payload: RoomCreate, session: Session = Depends(get_session)):
    row = Room(name=payload.name, columns_json=json.dumps(payload.columns), seat_capacity=payload.seat_capacity)
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**row.model_dump(), "columns": payload.columns, "capacity": room_capacity(row)}


@app.get("/rooms")
def list_rooms(session: Session = Depends(get_session)):
    rooms = session.exec(select(Room)).all()
    out = []
    for room in rooms:
        out.append({**room.model_dump(), "columns": json.loads(room.columns_json), "capacity": room_capacity(room)})
    return out


@app.put("/rooms/{room_id}")
def update_room(room_id: int, payload: RoomCreate, session: Session = Depends(get_session)):
    row = session.get(Room, room_id)
    if not row:
        raise HTTPException(404, "Room not found")
    row.name = payload.name
    row.columns_json = json.dumps(payload.columns)
    row.seat_capacity = payload.seat_capacity
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**row.model_dump(), "columns": payload.columns, "capacity": room_capacity(row)}


@app.delete("/rooms/{room_id}")
def delete_room(room_id: int, session: Session = Depends(get_session)):
    row = session.get(Room, room_id)
    if not row:
        raise HTTPException(404, "Room not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


@app.post("/import/exam-schedule")
def import_exam_schedule(file: UploadFile = File(...), session: Session = Depends(get_session)):
    text = parse_pdf_text(file)
    rows = extract_schedule_from_text(text)
    created = []
    for data in rows:
        obj = ExamSession(**data)
        session.add(obj)
        created.append(obj)
    session.commit()
    return {"inserted": len(created)}


@app.post("/import/students")
def import_students(file: UploadFile = File(...), session: Session = Depends(get_session)):
    text = parse_pdf_text(file)
    rows = extract_students_from_text(text)
    sessions = session.exec(select(ExamSession)).all()
    created = 0
    for row in rows:
        student = Student(**row)
        student.matched_exam_session_id = fuzzy_match_session(student.subject_code, sessions)
        session.add(student)
        created += 1
    session.commit()
    return {"inserted": created}


@app.get("/subject-pool")
def subject_pool(exam_date: date, exam_session_id: int | None = None, session: Session = Depends(get_session)):
    query = select(ExamSession).where(ExamSession.exam_date == exam_date)
    if exam_session_id:
        query = query.where(ExamSession.id == exam_session_id)
    sessions = session.exec(query).all()
    data = []
    for sess in sessions:
        count = len(
            session.exec(select(Student).where(Student.matched_exam_session_id == sess.id)).all()
        )
        data.append({
            "exam_session_id": sess.id,
            "subject_code": sess.subject_code,
            "subject_name": sess.subject_name,
            "student_count": count,
        })
    return data


@app.get("/seat-plan/room-sufficiency")
def room_sufficiency(exam_session_id: int, room_ids: list[int] = Query(default=[]), session: Session = Depends(get_session)):
    student_count = len(session.exec(select(Student).where(Student.matched_exam_session_id == exam_session_id)).all())
    rooms = [session.get(Room, rid) for rid in room_ids]
    valid_rooms = [r for r in rooms if r]
    total_capacity = sum(room_capacity(room) for room in valid_rooms)
    return {
        "student_count": student_count,
        "selected_capacity": total_capacity,
        "sufficient": total_capacity >= student_count,
        "remaining": max(student_count - total_capacity, 0),
    }


@app.post("/seat-plan", response_model=SeatPlan)
def create_seat_plan(payload: SeatPlanCreate, session: Session = Depends(get_session)):
    students = session.exec(select(Student).where(Student.matched_exam_session_id == payload.exam_session_id)).all()
    room = session.get(Room, payload.room_id)
    if not room:
        raise HTTPException(404, "Room not found")

    if payload.auto_generated:
        capacity = room_capacity(room)
        if len(students) > capacity:
            raise HTTPException(400, "Room capacity insufficient")
        mapping = {}
        cols = json.loads(room.columns_json)
        seats: list[str] = []
        for col_idx, desks in enumerate(cols, 1):
            for desk in range(1, desks + 1):
                for seat in range(1, room.seat_capacity + 1):
                    seats.append(f"C{col_idx}-D{desk}-S{seat}")
        for idx, student in enumerate(students):
            mapping[student.registration_no] = seats[idx]
    else:
        if not payload.mapping:
            raise HTTPException(400, "Manual mapping required")
        mapping = payload.mapping

    row = SeatPlan(
        exam_date=payload.exam_date,
        exam_session_id=payload.exam_session_id,
        room_id=payload.room_id,
        auto_generated=payload.auto_generated,
        mapping_json=json.dumps(mapping),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.get("/seat-plan")
def list_seat_plans(session: Session = Depends(get_session)):
    plans = session.exec(select(SeatPlan)).all()
    return [{**p.model_dump(), "mapping": json.loads(p.mapping_json)} for p in plans]


@app.get("/seat-plan/{plan_id}/export")
def export_plan_pdf(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(SeatPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Seat plan not found")
    exam_session = session.get(ExamSession, plan.exam_session_id)
    room = session.get(Room, plan.room_id)
    mapping = json.loads(plan.mapping_json)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, y, f"Seat Plan for {exam_session.subject_code} - {exam_session.subject_name}")
    y -= 20
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, y, f"Date: {plan.exam_date} | Room: {room.name}")
    y -= 30

    for reg_no, seat in mapping.items():
        pdf.drawString(40, y, f"{reg_no} -> {seat}")
        y -= 15
        if y < 50:
            pdf.showPage()
            y = 800
    pdf.save()
    buffer.seek(0)

    filename = f"seat-plan-{plan_id}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/")
def health():
    return {
        "name": "Exam Seat Planner API",
        "features": [
            "CRUD exam sessions, students, rooms",
            "PDF imports for exam schedules and student details",
            "Fuzzy subject code matching",
            "Subject pool and room sufficiency",
            "Manual/auto seat planning",
            "Seat plan PDF export",
        ],
    }
