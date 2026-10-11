# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Replaces hour/minute using IST shift start on a naive/UTC datetime without timezone conversion. | Punch in at 09:30 IST; replacing hours directly on UTC yields wrong time. | Converted `punch_in` to IST first, then computed difference. |
| 2 | `compute_work_hours` | Uses `round(..., 2)` which performs banker's rounding (round half to even). | 4.505 hours rounds to 4.50 instead of 4.51 (R4 requires half-up). | Used `decimal.Decimal` with `ROUND_HALF_UP`. |
| 3 | `compute_overtime` | Fails to handle overnight shifts where `shift_end` is on the next calendar day. | Overnight shift 22:00-06:00, punch out at 07:00 next day yields incorrect negative difference. | Added 1 day to the end datetime if `shift_end` <= `shift_start`. |
| 4 | `create_employee`, `punch_in` | Uses check-then-insert (`find_one` then `insert_one`) for duplicates instead of atomic constraints. | Sending two simultaneous create/punch-in requests inserts two records instead of returning 409. | Created unique indexes at app startup and caught `DuplicateKeyError`. |
| 5 | `list_employees` | `skip = page * page_size` skips the first page. | Requesting page 1, size 20 misses the first 20 items. | Changed to `skip = (page - 1) * page_size`. |
| 6 | `list_employees` | `total = db.employees.count_documents({})` ignores query filters. | Filtering by department returns the total employee count instead of department count. | Passed the query `q` to `count_documents(q)`. |
| 7 | `punch_in` | `datetime.fromtimestamp(...)` returns local naive datetime instead of UTC. | Running the server in different timezones produces different internal dates for the same epoch timestamp. | Used `datetime.fromtimestamp(..., tz=timezone.utc)`. |
| 8 | `list_attendance` | Fetches the entire collection into memory (`list(...)`) before sorting and paginating. | With 100,000 records, the API consumes massive memory and is extremely slow. | Changed to use MongoDB's `.sort()`, `.skip()`, and `.limit()`. |
| 9 | `punch_out`, `regularize_attendance` | PyMongo returns naive datetimes in UTC, causing `TypeError: can't subtract offset-naive and offset-aware datetimes` when subtracting from timezone-aware `out_utc`. | Any punch-out or regularization subtracting stored times crashes with 500 TypeError. | Added `ensure_utc` helper to ensure all retrieved datetimes are explicitly UTC-aware before math. |
| 10 | `format_timestamp` | Calling `.timestamp()` on naive UTC datetimes from PyMongo assumes local machine time (IST), shifting epoch by 5.5 hours. | Instants returned in JSON responses did not match original punch-in timestamps. | Enforced `ensure_utc` before computing epoch milliseconds. |
| 11 | `explain_endpoint` | MongoDB Atlas explain plans contain `bson.Timestamp` objects which default FastAPI `jsonable_encoder` cannot serialize. | Calling `/admin/explain/{endpoint}` raises `ValueError: TypeError('Timestamp' object is not iterable)`. | Sanitized explain documents using `clean_bson` via `bson.json_util`. |

**Examined and verified**:
- Index creation idempotency at application startup using `@app.on_event("startup")` ensuring zero crash restarts.
- Concurrency safety with optimistic locking on the audit history length.
