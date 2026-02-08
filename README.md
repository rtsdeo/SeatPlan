# Exam Seat Planner

FastAPI-based backend for exam seat planning workflows:

- CRUD for exam sessions
- CRUD for students (regular/partial) with auto fuzzy matching to subjects
- CRUD for rooms with desk-column arrangement and per-desk seat capacity
- PDF import/parsing for exam schedules and students
- Subject pool summary by date/session
- Room sufficiency checks for seat planning
- Manual and auto seat-plan generation
- Seat plan export to PDF

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open docs: `http://127.0.0.1:8000/docs`

## Notes on expected PDF patterns

The parser looks for text lines like:

- Schedule: `2026-05-10 | FN | MAT101 | Mathematics I`
- Students: `22CS001, Jane Doe, regular, MAT101`

If your notice format differs, update regex helpers in `app/main.py`.
