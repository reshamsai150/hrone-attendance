# DECISIONS.md

1. **Which indexes exist and why.**
   - A unique index on `employees.emp_code` to ensure fast lookups and atomic prevention of duplicate employees.
   - A unique compound index on `attendance_logs(emp_code, date)` to atomically prevent multiple punch-ins for the same day.
   - Indexes on fields like `department` and `date` in both collections to optimize filtering, sorting, and analytical aggregations without collection scans.

2. **What happens during simultaneous punch-in requests.**
   The unique index on `(emp_code, date)` in `attendance_logs` ensures only the first request succeeds in inserting the document. Any subsequent simultaneous requests encounter a `DuplicateKeyError` at the database level, which the API catches and correctly translates into a `409 Conflict` response.

3. **How leaderboard ties at the cutoff are handled.**
   Standard competition ranking assigns the same rank to tied employees, skipping subsequent ranks. The `limit` is applied strictly by checking if `rank <= limit`. Thus, if multiple employees tie at a rank at or below the cutoff limit, they are all returned, even if this causes the total number of items to exceed the limit.

4. **How employees without logs are included in department headcount.**
   The aggregation pipeline starts from the `employees` collection (or performs a `$lookup` originating from `employees` that matches the target date boundaries), ensuring every qualified employee counts toward the headcount regardless of whether they have any matching records in `attendance_logs`.

5. **One improvement for 100x the data volume.**
   For a hundredfold increase in data, analytical queries over `attendance_logs` would become expensive to compute on the fly. I would implement materialized views or pre-aggregated daily/monthly summary collections that update incrementally upon each punch-out or correction, avoiding heavy aggregations at read time.
