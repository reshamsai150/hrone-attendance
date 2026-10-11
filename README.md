# HROne Employee Attendance & Analytics API

## Overview
A high-performance FastAPI and MongoDB attendance verification and analytics service built to strict contract specifications.

It allows HR teams to:
- Add employees and view employee records.
- Record employee punch-in and punch-out times.
- Calculate working hours, late minutes, overtime, and half-days automatically.
- Correct attendance records while maintaining a history of changes.
- Generate monthly employee reports, department summaries, late-comer rankings, and attendance trends.
- Use database indexes to improve query performance.

## Setup and Run Instructions

### Prerequisites
- Python 3.11+
- MongoDB 6.0+

### Environment Variables
Set the following environment variables (or use a `.env` file):
- `MONGO_URI`: Your MongoDB connection URI (e.g., `mongodb://localhost:27017`)
- `MONGO_DB`: The target database name (e.g., `attendance_db`)

### Seeding Data
To load the sample dataset:
```bash
python sample_seed.py
```

### Running the Application
```bash
uvicorn app.main:app --port 8000
```

### Running Tests
Use `pytest` to run the test suite:
```bash
pytest
```

## API Documentation
The API strictly follows the OpenAPI specification provided in `openapi.yaml`. Once running, you can access the interactive Swagger documentation at `http://127.0.0.1:8000/docs`.

## Limitations
- Authentication and authorization are not implemented as per the assignment scope.
- Handling timezone conversions is strictly bound to IST (UTC+05:30) as required by the business rules.
- Analytics rely on real-time MongoDB aggregations; for extreme data volumes, a pre-aggregated solution would be recommended.

## Submission Contents
- `app/main.py`: The FastAPI application containing all endpoints and business logic.
- `REVIEW.md`: A review of the starter code defects and how they were fixed.
- `DECISIONS.md`: Design and implementation decisions regarding indexes, concurrency, and rules.
- `README.md`: Setup and execution instructions.
- `openapi.yaml`: The authoritative OpenAPI contract.
- `DATA_MODEL.md`: Documentation for the MongoDB schema.
- `sample_seed.py`: Script to populate the database with sample data.
- `requirements.txt`: Python dependencies.
