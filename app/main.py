import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

raw_mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017").strip(' "\'')
raw_mongo_db = os.getenv("MONGO_DB", "attendance_db").strip(' "\'')

client_kwargs = {"serverSelectionTimeoutMS": 5000}
try:
    import certifi
    client_kwargs["tlsCAFile"] = certifi.where()
except Exception:
    pass

client = MongoClient(raw_mongo_uri, **client_kwargs)
db = client[raw_mongo_db]

IST = timezone(timedelta(hours=5, minutes=30))

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import FileResponse

@app.get("/", include_in_schema=False)
def serve_index():
    index_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "HROne Employee Attendance & Analytics API is running. Visit /docs for Swagger UI."}

@app.on_event("startup")
def startup_db_client():
    try:
        client.admin.command("ping")
        print("[STARTUP] Connected to MongoDB Atlas successfully.")
        db.employees.create_index([("emp_code", ASCENDING)], unique=True)
        db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])
        db.employees.create_index([("joined_on", ASCENDING)])
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
        db.attendance_logs.create_index([("date", ASCENDING), ("emp_code", ASCENDING)])
        db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", ASCENDING)])
        db.attendance_logs.create_index([("punch_in", ASCENDING)])
        print("[STARTUP] Indexes ensured successfully.")
    except Exception as e:
        print(f"[STARTUP WARNING] MongoDB connection or index creation failed: {e}")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def truncate_to_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)

def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def get_ist_datetime(dt_utc: datetime) -> datetime:
    dt_utc = ensure_utc(dt_utc)
    return dt_utc.astimezone(IST)

def get_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = get_ist_datetime(punch_in_utc)
    date_ist = punch_in_ist.date()
    
    h_s, m_s = map(int, shift_start.split(":"))
    h_e, m_e = map(int, shift_end.split(":"))
    
    is_overnight = (h_e < h_s) or (h_e == h_s and m_e <= m_s)
    if is_overnight:
        shift_end_time = datetime.strptime(shift_end, "%H:%M").time()
        if punch_in_ist.time() < shift_end_time:
            date_ist -= timedelta(days=1)
            
    return date_ist.isoformat()

def compute_late_minutes(punch_in_utc: datetime, shift_start: str) -> int:
    punch_in_ist = get_ist_datetime(punch_in_utc)
    h, m = map(int, shift_start.split(":"))
    start_ist = punch_in_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in_ist - start_ist).total_seconds() / 60)
    return minutes if minutes > 10 else 0

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    diff_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    hours = diff_seconds / 3600
    return float(Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out_utc: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    h_s, m_s = map(int, shift_start.split(":"))
    h_e, m_e = map(int, shift_end.split(":"))
    
    end_ist = datetime.fromisoformat(date_str).replace(hour=h_e, minute=m_e, tzinfo=IST)
    if (h_e < h_s) or (h_e == h_s and m_e <= m_s):
        end_ist += timedelta(days=1)
        
    punch_out_ist = get_ist_datetime(punch_out_utc)
    overtime_min = int((punch_out_ist - end_ist).total_seconds() / 60)
    return overtime_min if overtime_min >= 30 else 0

def compute_half_day(work_hours: float) -> bool:
    return work_hours < 4.50

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
from typing import Annotated

StrictEpochMillis = Annotated[int, Field(ge=100000000000, le=4102444800000)]

class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictEpochMillis] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictEpochMillis] = None

def format_timestamp(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    dt = ensure_utc(dt)
    return int(dt.timestamp() * 1000)

def map_employee(doc) -> dict:
    doc.pop("_id", None)
    if "created_at" in doc and doc["created_at"]:
        doc["created_at"] = format_timestamp(doc["created_at"])
    return doc

def map_attendance(doc) -> dict:
    doc.pop("_id", None)
    doc["punch_in"] = format_timestamp(doc.get("punch_in"))
    doc["punch_out"] = format_timestamp(doc.get("punch_out"))
    if "history" not in doc or doc["history"] is None:
        doc["history"] = []
    else:
        for entry in doc["history"]:
            entry["at"] = format_timestamp(entry.get("at"))
            if "changes" in entry and isinstance(entry["changes"], dict):
                for chk, chv in entry["changes"].items():
                    if isinstance(chv, dict):
                        if isinstance(chv.get("from"), datetime):
                            chv["from"] = format_timestamp(chv["from"])
                        if isinstance(chv.get("to"), datetime):
                            chv["to"] = format_timestamp(chv["to"])
    if "half_day" not in doc or doc["half_day"] is None:
        doc["half_day"] = False
    return doc

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command('ping')
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Service Unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")
        
    doc = body.model_dump()
    doc["created_at"] = truncate_to_seconds(datetime.now(timezone.utc))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    
    return map_employee(doc)

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q).sort("emp_code", ASCENDING).skip(skip).limit(page_size))
    return {"items": [map_employee(i) for i in items], "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(422, "Invalid status")
        
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    if body.punched_at:
        ts_utc = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts_utc = datetime.now(timezone.utc)
        
    ts_utc = truncate_to_seconds(ts_utc)
    
    d = get_attendance_date(ts_utc, emp["shift_start"], emp["shift_end"])
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts_utc, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    return map_attendance(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    if body.punched_at:
        out_utc = truncate_to_seconds(datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc))
    else:
        out_utc = truncate_to_seconds(datetime.now(timezone.utc))
        
    # Find most recent record with punch_in <= out_utc
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": out_utc}},
        sort=[("punch_in", -1)]
    )
    
    if not record:
        raise HTTPException(404, "No valid punch-in found")
        
    if record.get("punch_out") is not None:
        raise HTTPException(409, "Record already punched out")
        
    punch_in_utc = ensure_utc(record["punch_in"])
    diff_sec = (out_utc - punch_in_utc).total_seconds()
    if diff_sec < 0 or diff_sec > 86400:
        raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        
    work_hours = compute_work_hours(punch_in_utc, out_utc)
    overtime_minutes = compute_overtime(out_utc, emp["shift_start"], emp["shift_end"], record["date"])
    half_day = compute_half_day(work_hours)
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": out_utc,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day
        }},
        return_document=True
    )
    
    if not res:
        raise HTTPException(409, "Record already punched out by another request")
        
    return map_attendance(res)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    
    items = list(db.attendance_logs.find(q)
                 .sort([("date", -1), ("emp_code", 1)])
                 .skip(skip)
                 .limit(page_size))
                 
    return {"items": [map_attendance(d) for d in items], "total": total, "page": page, "page_size": page_size}

from typing import Optional, Dict, Any

from typing import Literal

class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictEpochMillis] = None
    punch_out: Optional[StrictEpochMillis] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Record not found")

    # Capture original state
    orig = {
        "status": record.get("status"),
        "punch_in": ensure_utc(record.get("punch_in")),
        "punch_out": ensure_utc(record.get("punch_out")),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes"),
        "overtime_minutes": record.get("overtime_minutes"),
        "half_day": record.get("half_day", False)
    }

    # Determine new base values
    new_status = body.status if body.status is not None else orig["status"]
    
    new_punch_in = None
    if body.punch_in is not None:
        new_punch_in = truncate_to_seconds(datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc))
    else:
        new_punch_in = orig["punch_in"]

    new_punch_out = None
    if body.punch_out is not None:
        new_punch_out = truncate_to_seconds(datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc))
    else:
        new_punch_out = orig["punch_out"]

    # Rules
    if new_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times for ABSENT or LEAVE")
        new_punch_in = None
        new_punch_out = None
    else:
        if new_punch_in is None:
            raise HTTPException(422, "Presence status requires a punch_in")
            
        emp = db.employees.find_one({"emp_code": emp_code})
        if not emp:
            raise HTTPException(404, "Employee not found")
            
        # check punch_in remains on the correct attendance date
        computed_date = get_attendance_date(new_punch_in, emp["shift_start"], emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, "punch_in does not fall on the original attendance date")
            
        if new_punch_out is not None:
            diff = (new_punch_out - new_punch_in).total_seconds()
            if diff < 0 or diff > 86400:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    # Recompute derived fields
    new_work_hours = None
    new_late_minutes = 0
    new_overtime_minutes = 0
    new_half_day = False
    
    if new_status not in ["ABSENT", "LEAVE"]:
        emp = db.employees.find_one({"emp_code": emp_code})
        new_late_minutes = compute_late_minutes(new_punch_in, emp["shift_start"])
        if new_punch_out is not None:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_overtime_minutes = compute_overtime(new_punch_out, emp["shift_start"], emp["shift_end"], date)
            new_half_day = compute_half_day(new_work_hours)

    curr = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day
    }

    # Determine what changed
    changes = {}
    for k in orig.keys():
        old_val = orig[k]
        new_val = curr[k]
        
        # handle datetime comparisons vs timestamps
        cmp_old = format_timestamp(old_val) if isinstance(old_val, datetime) else old_val
        cmp_new = format_timestamp(new_val) if isinstance(new_val, datetime) else new_val
        
        if cmp_old != cmp_new:
            changes[k] = {"from": cmp_old, "to": cmp_new}
            
    if not changes:
        raise HTTPException(422, "No changes detected")
        
    history_entry = {
        "at": truncate_to_seconds(datetime.now(timezone.utc)),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    
    update_doc = {
        "$set": curr,
        "$push": {"history": history_entry}
    }
    
    # Use find_one_and_update for concurrency using the exact history length as versioning
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "history": {"$size": len(record.get("history", []))}},
        update_doc,
        return_document=True
    )
    
    if not res:
        raise HTTPException(409, "Concurrent modification detected. Please try again.")
        
    return map_attendance(res)
import calendar
from fastapi import Query

# --------------------------------------------------------------------------- #
# Analytics (Phase D)
# --------------------------------------------------------------------------- #

def get_month_boundaries(month: str):
    y, m = map(int, month.split("-"))
    start_date = f"{month}-01"
    last_day = calendar.monthrange(y, m)[1]
    end_date = f"{month}-{last_day}"
    return start_date, end_date

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    start_date, end_date = get_month_boundaries(month)
    
    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
        {"$addFields": {
            "date_type": {"$dateFromString": {"dateString": "$date"}}
        }},
        {"$addFields": {
            "is_working_day": {"$in": [{"$isoDayOfWeek": "$date_type"}, [1, 2, 3, 4, 5]]}
        }},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            "$is_working_day",
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
                        ]},
                        {"$cond": ["$half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]
    
    res = list(db.attendance_logs.aggregate(pipeline))
    stats = res[0] if res else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0
    }
    
    join_date = emp["joined_on"]
    actual_start = max(start_date, join_date)
    working_days = 0
    
    if actual_start <= end_date:
        d = datetime.strptime(actual_start, "%Y-%m-%d")
        end_d = datetime.strptime(end_date, "%Y-%m-%d")
        while d <= end_d:
            if d.weekday() < 5:
                working_days += 1
            d += timedelta(days=1)
            
    attendance_pct = None
    if working_days > 0:
        val = (stats["present_days"] / working_days) * 100
        attendance_pct = float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": stats["present_days"],
        "leave_days": stats["leave_days"],
        "late_count": stats["late_count"],
        "total_late_minutes": stats["total_late_minutes"],
        "total_overtime_minutes": stats["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start_date, end_date = get_month_boundaries(month)
    
    match_stage = {"joined_on": {"$lte": end_date}}
    if department:
        match_stage["department"] = department

    pipeline = [
        {"$match": match_stage},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp"]},
                    "date": {"$gte": start_date, "$lte": end_date}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "date_type": {"$cond": [{"$eq": ["$logs.date", None]}, None, {"$dateFromString": {"dateString": "$logs.date"}}]}
        }},
        {"$addFields": {
            "is_working_day": {"$cond": [{"$eq": ["$date_type", None]}, False, {"$in": [{"$isoDayOfWeek": "$date_type"}, [1, 2, 3, 4, 5]]}]}
        }},
        {"$group": {
            "_id": {"dept": "$department", "emp": "$emp_code"},
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            "$is_working_day",
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}
                        ]},
                        {"$cond": ["$logs.half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
            "work_hours_array": {
                "$push": {
                    "$cond": [
                        {"$and": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ne": ["$logs.work_hours", None]}]},
                        "$logs.work_hours",
                        "$$REMOVE"
                    ]
                }
            }
        }},
        {"$group": {
            "_id": "$_id.dept",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
            "all_work_hours": {"$push": "$work_hours_array"}
        }},
        {"$sort": {"_id": 1}}
    ]
    
    results = db.employees.aggregate(pipeline)
    items = []
    
    for r in results:
        # flat map work hours
        flat_hours = [h for sublist in r["all_work_hours"] for h in sublist]
        avg_work_hours = None
        if flat_hours:
            avg_val = sum(flat_hours) / len(flat_hours)
            avg_work_hours = float(Decimal(str(avg_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            
        items.append({
            "department": r["_id"],
            "headcount": r["headcount"],
            "present_days": r["present_days"],
            "avg_work_hours": avg_work_hours,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"]
        })
        
    return {"month": month, "items": items}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    start_date, end_date = get_month_boundaries(month)
    
    match_stage = {"date": {"$gte": start_date, "$lte": end_date}, "late_minutes": {"$gt": 0}}
    
    pipeline = [
        {"$match": match_stage},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"}
    ]
    
    if department:
        pipeline.append({"$match": {"emp.department": department}})
        
    pipeline.extend([
        {"$sort": {"total_late_minutes": -1, "_id": 1}}
    ])
    
    res = list(db.attendance_logs.aggregate(pipeline))
    
    items = []
    current_rank = 1
    previous_late = None
    
    for i, r in enumerate(res):
        if previous_late is not None and r["total_late_minutes"] < previous_late:
            current_rank = i + 1
            
        if current_rank > limit:
            break
            
        items.append({
            "rank": current_rank,
            "emp_code": r["_id"],
            "name": r["emp"]["name"],
            "department": r["emp"]["department"],
            "total_late_minutes": r["total_late_minutes"],
            "late_count": r["late_count"]
        })
        previous_late = r["total_late_minutes"]
        
    return {"month": month, "items": items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    if from_ > to:
        raise HTTPException(422, "from date cannot be after to date")
    
    start_dt = datetime.strptime(from_, "%Y-%m-%d")
    end_dt = datetime.strptime(to, "%Y-%m-%d")
    if (end_dt - start_dt).days > 92:
        raise HTTPException(422, "date range exceeds 92 days")
        
    emp_count = db.employees.count_documents({"department": department})
    if emp_count == 0:
        raise HTTPException(404, "Department not found")

    pipeline = [
        # Match all employees in the department to get joined_on dates
        {"$match": {"department": department, "joined_on": {"$lte": to}}},
        # Lookup attendance logs in the date range
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp"]},
                    "date": {"$gte": from_, "$lte": to}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        # Group by date to aggregate stats
        {"$group": {
            "_id": "$logs.date",
            "present_count": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": ["$logs.half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}
        }},
        # Add a date type field for densify
        {"$addFields": {
            "date_type": {"$cond": [{"$eq": ["$_id", None]}, None, {"$dateFromString": {"dateString": "$_id"}}]}
        }},
        # Filter out the null date (from employees with no logs)
        {"$match": {"date_type": {"$ne": None}}},
        # Densify to fill missing dates
        {"$densify": {
            "field": "date_type",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [datetime.strptime(from_, "%Y-%m-%d"), datetime.strptime(to, "%Y-%m-%d") + timedelta(days=1)]
            }
        }},
        # Re-format date back to string and initialize nulls
        {"$addFields": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_type"}},
            "is_working_day": {"$in": [{"$isoDayOfWeek": "$date_type"}, [1, 2, 3, 4, 5]]},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]}
        }},
        # Lookup headcount for each date
        {"$lookup": {
            "from": "employees",
            "let": {"d": "$date"},
            "pipeline": [
                {"$match": {
                    "department": department,
                    "$expr": {"$lte": ["$joined_on", "$$d"]}
                }},
                {"$count": "headcount"}
            ],
            "as": "hc"
        }},
        {"$addFields": {
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.headcount", 0]}, 0]}
        }},
        {"$project": {"hc": 0, "date_type": 0, "_id": 0}},
        # Calculate attendance rate
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
                    None
                ]
            }
        }},
        # Sort chronologically for setWindowFields
        {"$sort": {"date": 1}},
        # Moving average 7d
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]}
                }
            }
        }}
    ]

    res = list(db.employees.aggregate(pipeline))
    
    # We still need to handle cases where there are NO logs for the department in the range, 
    # as the densify might fail if the input is empty. A quick functional fill ensures safety:
    existing_dates = {r["date"]: r for r in res if "date" in r}
    final_items = []
    
    d = start_dt
    while d <= end_dt:
        d_str = d.strftime("%Y-%m-%d")
        if d_str in existing_dates:
            item = existing_dates[d_str]
            # Rounding to 4 decimal places half up
            if item.get("moving_avg_7d") is not None:
                item["moving_avg_7d"] = float(Decimal(str(item["moving_avg_7d"])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            final_items.append(item)
        else:
            # Fallback if aggregation returned nothing
            is_working = d.weekday() < 5
            hc = db.employees.count_documents({"department": department, "joined_on": {"$lte": d_str}})
            rate = 0.0 if (is_working and hc > 0) else None
            
            final_items.append({
                "date": d_str,
                "is_working_day": is_working,
                "headcount": hc,
                "present_count": 0,
                "late_count": 0,
                "attendance_rate": rate,
                "moving_avg_7d": rate # In a purely empty set, avg of N zeros is zero
            })
        d += timedelta(days=1)

    return {"department": department, "items": final_items}

from bson import json_util
import json

def clean_bson(doc: Any) -> Any:
    return json.loads(json_util.dumps(doc))

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, request: Request):
    params = request.query_params
    
    if endpoint == "attendance_list":
        q = {}
        if "emp_code" in params:
            q["emp_code"] = params["emp_code"]
        if "date_from" in params or "date_to" in params:
            q["date"] = {}
            if "date_from" in params:
                q["date"]["$gte"] = params["date_from"]
            if "date_to" in params:
                q["date"]["$lte"] = params["date_to"]
        if "status" in params:
            q["status"] = params["status"]
            
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
        skip = (page - 1) * page_size
        
        explain_res = db.command(
            "explain", 
            {
                "find": "attendance_logs", 
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip,
                "limit": page_size
            },
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_bson(explain_res)}
        
    elif endpoint == "employee_monthly":
        emp_code = params.get("emp_code")
        month = params.get("month")
        if not emp_code or not month:
            raise HTTPException(422, "Missing parameters")
        start_date, end_date = get_month_boundaries(month)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
            {"$group": {"_id": None}}
        ]
        explain_res = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_bson(explain_res)}
        
    elif endpoint == "department_summary":
        month = params.get("month")
        if not month:
            raise HTTPException(422, "Missing month")
        start_date, end_date = get_month_boundaries(month)
        match_stage = {"joined_on": {"$lte": end_date}}
        if "department" in params:
            match_stage["department"] = params["department"]
            
        pipeline = [
            {"$match": match_stage},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$emp"]},
                        "date": {"$gte": start_date, "$lte": end_date}
                    }}
                ],
                "as": "logs"
            }},
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {"$group": {"_id": "$department"}}
        ]
        explain_res = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "employees", "explain": clean_bson(explain_res)}
        
    elif endpoint == "late_leaderboard":
        month = params.get("month")
        if not month:
            raise HTTPException(422, "Missing month")
        start_date, end_date = get_month_boundaries(month)
        match_stage = {"date": {"$gte": start_date, "$lte": end_date}, "late_minutes": {"$gt": 0}}
        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }},
            {"$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }},
            {"$unwind": "$emp"}
        ]
        if "department" in params:
            pipeline.append({"$match": {"emp.department": params["department"]}})
        explain_res = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_bson(explain_res)}
        
    elif endpoint == "department_trend":
        department = params.get("department")
        from_ = params.get("from")
        to = params.get("to")
        if not department or not from_ or not to:
            raise HTTPException(422, "Missing parameters")
        pipeline = [
            {"$match": {"department": department, "joined_on": {"$lte": to}}},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$emp"]},
                        "date": {"$gte": from_, "$lte": to}
                    }}
                ],
                "as": "logs"
            }}
        ]
        explain_res = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "employees", "explain": clean_bson(explain_res)}
        
    else:
        raise HTTPException(422, "Unknown endpoint")
