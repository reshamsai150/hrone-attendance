import os
import sys
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

BASE_URL = "http://127.0.0.1:8000"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
TEST_DB_NAME = "attendance_qa_test_db"

print("=" * 70)
print("HROne ATTENDANCE & ANALYTICS: FULL QA AUDIT AND INTEGRATION SUITE")
print("=" * 70)

# Connect directly to MongoDB for test database provisioning
client = MongoClient(MONGO_URI)
test_db = client[TEST_DB_NAME]

def setup_isolated_test_data():
    print("\n[STEP 1] Setting up isolated test database:", TEST_DB_NAME)
    test_db.employees.drop()
    test_db.attendance_logs.drop()

    # Recreate required indexes
    test_db.employees.create_index([("emp_code", 1)], unique=True)
    test_db.employees.create_index([("department", 1), ("joined_on", 1)])
    test_db.employees.create_index([("joined_on", 1)])
    test_db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    test_db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    test_db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])
    test_db.attendance_logs.create_index([("punch_in", 1)])

    # 1. EMPLOYEES:
    # EMP0001 - Priya Sharma - Engineering - Joined 2026-01-05
    # EMP0002 - Rahul Kumar - Engineering - Joined 2026-07-13 (Mid-month joiner)
    # EMP0003 - Sneha Patel - HR - Joined 2026-02-01
    # EMP0004 - Arjun Reddy - Finance - Joined 2026-02-01
    # EMP0005 - Ananya Rao - Engineering - Joined 2026-03-10 (Overnight shift: 22:00 - 06:00)
    # EMP0006 - Zero Logs - Engineering - Joined 2026-01-01 (Never punches in)

    employees = [
        {
            "emp_code": "EMP0001",
            "name": "Priya Sharma",
            "email": "priya.sharma@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
            "created_at": datetime(2026, 1, 5, 4, 0, 0, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0002",
            "name": "Rahul Kumar",
            "email": "rahul.kumar@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-13",
            "created_at": datetime(2026, 7, 13, 4, 0, 0, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0003",
            "name": "Sneha Patel",
            "email": "sneha.patel@example.com",
            "department": "HR",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-02-01",
            "created_at": datetime(2026, 2, 1, 4, 0, 0, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0004",
            "name": "Arjun Reddy",
            "email": "arjun.reddy@example.com",
            "department": "Finance",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-02-01",
            "created_at": datetime(2026, 2, 1, 4, 0, 0, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0005",
            "name": "Ananya Rao",
            "email": "ananya.rao@example.com",
            "department": "Engineering",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-03-10",
            "created_at": datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0006",
            "name": "Zero Logs Worker",
            "email": "zero.logs@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
            "created_at": datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone.utc)
        }
    ]
    test_db.employees.insert_many(employees)
    print(f"-> Seeded {len(employees)} employees in test DB.")

    # 2. ATTENDANCE LOGS:
    # 2026-07-06 (Monday):
    # - EMP0001: PRESENT, in 09:28 (03:58 UTC), out 18:35 (13:05 UTC) -> 9.12 hrs, 0 late, 0 overtime, half_day: false
    # - EMP0003: PRESENT, in 10:05 (04:35 UTC), out 19:10 (13:40 UTC) -> 9.08 hrs, 35 late, 40 overtime, half_day: false
    # - EMP0005: Overnight shift, in 21:55 (16:25 UTC on 6th), out 06:40 (01:10 UTC on 7th) -> 8.75 hrs, 0 late, 40 overtime, half_day: false
    #
    # 2026-07-07 (Tuesday):
    # - EMP0001: PRESENT, in 09:28 (03:58 UTC), out 18:30 (13:00 UTC) -> 9.03 hrs, 0 late, 0 overtime
    # - EMP0004: WFH, in 09:30 (04:00 UTC), out 13:00 (07:30 UTC) -> 3.50 hrs (< 4.50), half_day: true
    #
    # 2026-07-08 (Wednesday):
    # - EMP0003: LEAVE (punch_in: null, punch_out: null)
    # - EMP0004: ABSENT (punch_in: null, punch_out: null)
    #
    # 2026-07-11 (Saturday - Weekend!):
    # - EMP0001: ON_DUTY, in 09:30 (04:00 UTC), out 18:30 (13:00 UTC) -> 9.00 hrs, weekend record!
    #
    # Ties for leaderboard:
    # Both EMP0001 and EMP0004 have records to test ties if given equal late minutes!
    logs = [
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc),
            "work_hours": 9.12,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0003",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 4, 35, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc),
            "work_hours": 9.08,
            "late_minutes": 35,
            "overtime_minutes": 40,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0005",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 16, 25, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 1, 10, 0, tzinfo=timezone.utc),
            "work_hours": 8.75,
            "late_minutes": 0,
            "overtime_minutes": 40,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0001",
            "date": "2026-07-07",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 7, 3, 58, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 13, 0, 0, tzinfo=timezone.utc),
            "work_hours": 9.03,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0004",
            "date": "2026-07-07",
            "status": "WFH",
            "punch_in": datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 7, 30, 0, tzinfo=timezone.utc),
            "work_hours": 3.5,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": True,
            "history": []
        },
        {
            "emp_code": "EMP0003",
            "date": "2026-07-08",
            "status": "LEAVE",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0004",
            "date": "2026-07-08",
            "status": "ABSENT",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": []
        },
        {
            "emp_code": "EMP0001",
            "date": "2026-07-11",
            "status": "ON_DUTY",
            "punch_in": datetime(2026, 7, 11, 4, 0, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 11, 13, 0, 0, tzinfo=timezone.utc),
            "work_hours": 9.0,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": []
        }
    ]
    test_db.attendance_logs.insert_many(logs)
    print(f"-> Seeded {len(logs)} attendance logs in test DB.")

if __name__ == "__main__":
    setup_isolated_test_data()
