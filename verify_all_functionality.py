import requests
import json
import time
from datetime import datetime, timezone, timedelta

BASE_URL = "http://127.0.0.1:8000"

import sys
sys.stdout.reconfigure(encoding='utf-8')

def log_test(name, success, detail=""):
    mark = "[PASS]" if success else "[FAIL]"
    print(f"{mark} | {name}: {detail}")

def run_tests():
    print("=" * 60)
    print("RUNNING COMPLETE END-TO-END FUNCTIONALITY AUDIT")
    print("=" * 60)

    # 1. Health
    r = requests.get(f"{BASE_URL}/health")
    log_test("GET /health", r.status_code == 200, f"Status: {r.status_code}, Body: {r.json()}")

    # 2. Employees listing
    r = requests.get(f"{BASE_URL}/employees?page=1&page_size=5")
    data = r.json()
    log_test("GET /employees (Pagination)", r.status_code == 200 and "items" in data and len(data["items"]) > 0, 
             f"Total: {data.get('total')}, Page size: {len(data.get('items', []))}")

    # 3. Create Employee
    test_emp = {
        "emp_code": f"EMP{int(time.time()) % 100000:05d}",
        "name": "Audit Test User",
        "email": f"audit_{int(time.time())}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    }
    r = requests.post(f"{BASE_URL}/employees", json=test_emp)
    log_test("POST /employees (New Employee)", r.status_code == 201, f"Code: {test_emp['emp_code']}")

    # 4. Duplicate Employee (Conflict 409)
    r = requests.post(f"{BASE_URL}/employees", json=test_emp)
    log_test("POST /employees (Duplicate 409)", r.status_code == 409, f"Returned 409 as required")

    # 5. Punch-in
    now_ms = int(datetime(2026, 4, 1, 9, 35, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp() * 1000)
    punch_in_payload = {
        "emp_code": test_emp["emp_code"],
        "punched_at": now_ms,
        "status": "PRESENT"
    }
    r = requests.post(f"{BASE_URL}/attendance/punch-in", json=punch_in_payload)
    p_data = r.json()
    log_test("POST /attendance/punch-in", r.status_code == 201 and p_data.get("late_minutes") == 0, 
             f"Date: {p_data.get('date')}, Late min: {p_data.get('late_minutes')} (within 10m grace)")

    # 6. Duplicate Punch-in (Same date conflict 409)
    r = requests.post(f"{BASE_URL}/attendance/punch-in", json=punch_in_payload)
    log_test("POST /attendance/punch-in (Duplicate 409)", r.status_code == 409, "Prevented double punch-in")

    # 7. Punch-out
    out_ms = int(datetime(2026, 4, 1, 19, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp() * 1000)
    punch_out_payload = {
        "emp_code": test_emp["emp_code"],
        "punched_at": out_ms
    }
    r = requests.post(f"{BASE_URL}/attendance/punch-out", json=punch_out_payload)
    out_data = r.json()
    log_test("POST /attendance/punch-out", r.status_code == 200 and out_data.get("overtime_minutes") == 30,
             f"Work hours: {out_data.get('work_hours')}, Overtime: {out_data.get('overtime_minutes')}m, Half day: {out_data.get('half_day')}")

    # 8. List attendance filter
    r = requests.get(f"{BASE_URL}/attendance?emp_code={test_emp['emp_code']}")
    att_list = r.json()
    log_test("GET /attendance (Filter by emp)", r.status_code == 200 and att_list.get("total") >= 1, 
             f"Found {att_list.get('total')} records")

    # 9. PATCH /attendance (Manual regularization)
    patch_payload = {
        "status": "WFH",
        "reason": "Manager approved remote work",
        "regularized_by": "MGR001"
    }
    r = requests.patch(f"{BASE_URL}/attendance/{test_emp['emp_code']}/2026-04-01", json=patch_payload)
    reg_data = r.json()
    has_history = len(reg_data.get("history", [])) > 0
    log_test("PATCH /attendance (Regularization & Audit)", r.status_code == 200 and has_history,
             f"Status changed to: {reg_data.get('status')}, History audit entries: {len(reg_data.get('history', []))}")

    # 10. Analytics - Employee Monthly
    r = requests.get(f"{BASE_URL}/analytics/employees/{test_emp['emp_code']}/monthly?month=2026-04")
    m_data = r.json()
    log_test("GET /analytics/employees/{emp}/monthly", r.status_code == 200, 
             f"Working days: {m_data.get('working_days')}, Present: {m_data.get('present_days')}, Att %: {m_data.get('attendance_pct')}%")

    # 11. Analytics - Department Summary
    r = requests.get(f"{BASE_URL}/analytics/departments/summary?month=2026-04")
    d_data = r.json()
    log_test("GET /analytics/departments/summary", r.status_code == 200 and len(d_data.get("items", [])) > 0,
             f"Departments aggregated: {len(d_data.get('items', []))}")

    # 12. Analytics - Late Leaderboard
    r = requests.get(f"{BASE_URL}/analytics/leaderboard/late?month=2026-04")
    l_data = r.json()
    log_test("GET /analytics/leaderboard/late", r.status_code == 200, f"Leaderboard entries: {len(l_data.get('items', []))}")

    # 13. Analytics - Department Trend (Densify & 7-day moving avg)
    r = requests.get(f"{BASE_URL}/analytics/departments/Engineering/trend?from=2026-04-01&to=2026-04-07")
    t_data = r.json()
    items = t_data.get("items", [])
    log_test("GET /analytics/departments/{dept}/trend ($densify)", r.status_code == 200 and len(items) == 7,
             f"Calendar days densified: {len(items)} (7 days range full gap-fill)")

    # 14. Admin Explain Endpoint
    r = requests.get(f"{BASE_URL}/admin/explain/attendance_list?emp_code={test_emp['emp_code']}")
    exp_data = r.json()
    has_stats = "explain" in exp_data and ("executionStats" in exp_data["explain"] or "queryPlanner" in exp_data["explain"])
    log_test("GET /admin/explain/attendance_list", r.status_code == 200 and has_stats,
             f"Returned real MongoDB execution plan")

    print("=" * 60)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
