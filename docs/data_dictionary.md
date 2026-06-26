# AeroOps Database Data Dictionary

This document details the SQLite database schema for the synthetic AeroOps data layer. All table, column, status, and severity details are specified here.

- **Snapshot Date:** 2026-06-24
- **Dataset Version:** 1.0.0

---

## Tables

### 1. `aircraft`
Represents the physical aircraft prototypes or fleet leads under development.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^AC-\d{3}$`) | Unique stable aircraft identifier |
| `name` | TEXT | NOT NULL | Human-readable name of the aircraft |
| `status` | TEXT | NOT NULL, CHECK (`green`, `amber`, `red`) | Operational program health status |
| `responsible_org` | TEXT | NOT NULL | The organization owning this aircraft's timeline |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp of creation |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp of last update |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 2. `milestones`
Major schedule events for a specific aircraft (e.g., Flight Test Clearance).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^MS-\d{3}-[A-Z0-9-]+$`) | Unique milestone identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `name` | TEXT | NOT NULL | Description of milestone |
| `planned_date` | TEXT | NOT NULL (YYYY-MM-DD) | Baselined date |
| `forecast_date` | TEXT | NOT NULL (YYYY-MM-DD) | Projected/actual date |
| `status` | TEXT | NOT NULL, CHECK (`complete`, `on_track`, `at_risk`, `delayed`) | Schedule state of milestone |
| `responsible_role` | TEXT | NOT NULL | Responsible person/role for milestone execution |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 3. `defects`
Discovered mechanical, electrical, or software defects.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^DEF-\d{3}-\d{3}$`) | Unique defect identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `title` | TEXT | NOT NULL | Brief summary |
| `description` | TEXT | NOT NULL | Detailed defect description |
| `severity` | TEXT | NOT NULL, CHECK (`low`, `medium`, `high`, `critical`) | Severity ranking |
| `status` | TEXT | NOT NULL, CHECK (`open`, `in_progress`, `closed`) | Current state |
| `discovered_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `closed_at` | TEXT | NULLABLE | ISO 8601 UTC timestamp (null if open) |
| `responsible_role` | TEXT | NOT NULL | Owner role to resolve the defect |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 4. `test_events`
Planned, aborted, or completed flight/ground test executions.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^TEST-\d{3}-\d{3}$`) | Unique test identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `name` | TEXT | NOT NULL | Name of the test event |
| `status` | TEXT | NOT NULL, CHECK (`planned`, `blocked`, `in_progress`, `completed`, `aborted`) | Current status of test event |
| `responsible_role` | TEXT | NOT NULL | Test conductor or director role |
| `scheduled_date` | TEXT | NOT NULL (YYYY-MM-DD) | Scheduled date |
| `started_at` | TEXT | NULLABLE | Actual start UTC timestamp |
| `completed_at` | TEXT | NULLABLE | Actual completion/abort UTC timestamp |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 5. `maintenance_tasks`
Operational inspection and rigging tasks related to test support.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^MNT-\d{3}-\d{3}$`) | Unique maintenance task identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `title` | TEXT | NOT NULL | Brief description of task |
| `description` | TEXT | NOT NULL | Detailed maintenance description |
| `status` | TEXT | NOT NULL, CHECK (`scheduled`, `in_progress`, `completed`, `deferred`) | Maintenance status |
| `responsible_role` | TEXT | NOT NULL | Assigned maintenance role |
| `due_date` | TEXT | NOT NULL (YYYY-MM-DD) | Target due date |
| `completed_at` | TEXT | NULLABLE | Actual completion UTC timestamp |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 6. `parts_constraints`
Part requirements and supply chain tracking for testbeds.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^PART-[A-Z0-9-]+$`) | Unique part constraint identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `part_number` | TEXT | NOT NULL | Part catalog number |
| `description` | TEXT | NOT NULL | Description of part |
| `status` | TEXT | NOT NULL, CHECK (`awaiting_delivery`, `delivered`, `delayed`) | Procurement status |
| `responsible_org` | TEXT | NOT NULL | Procurement organization |
| `needed_by` | TEXT | NOT NULL (YYYY-MM-DD) | Required arrival date |
| `estimated_arrival` | TEXT | NULLABLE (YYYY-MM-DD) | Estimated arrival date |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 7. `change_requests`
Engineering config changes needed before resuming blocked tests.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^CR-\d{3}$`) | Unique change request identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `title` | TEXT | NOT NULL | Title of change request |
| `description` | TEXT | NOT NULL | Scope details |
| `status` | TEXT | NOT NULL, CHECK (`pending_review`, `approved`, `rejected`, `implemented`) | Status of review |
| `responsible_role` | TEXT | NOT NULL | Reviewing board or role |
| `submitted_at` | TEXT | NOT NULL | Submission UTC timestamp |
| `approved_at` | TEXT | NULLABLE | Approval UTC timestamp |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

---

### 8. `schedule_dependencies`
Links blocked test events to their blocker dependencies.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `source_id` | TEXT | PRIMARY KEY, CHECK (regexp `^DEP-\d{3}-\d{3}$`) | Unique dependency link identifier |
| `aircraft_id` | TEXT | NOT NULL, FOREIGN KEY to `aircraft.source_id` | Associated aircraft |
| `blocked_test_id` | TEXT | NOT NULL, FOREIGN KEY to `test_events.source_id` | The test event being blocked |
| `blocker_defect_id` | TEXT | NULLABLE, FOREIGN KEY to `defects.source_id` | The blocking defect |
| `blocker_parts_constraint_id` | TEXT | NULLABLE, FOREIGN KEY to `parts_constraints.source_id` | The blocking part constraint |
| `blocker_change_request_id` | TEXT | NULLABLE, FOREIGN KEY to `change_requests.source_id` | The blocking change request |
| `blocker_maintenance_task_id` | TEXT | NULLABLE, FOREIGN KEY to `maintenance_tasks.source_id` | The blocking maintenance task |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp |
| `synthetic_data` | INTEGER | NOT NULL, CHECK (value = 1) | Hardcoded flag for synthetic data safety |

> [!NOTE]
> `schedule_dependencies` has a CHECK constraint requiring that **exactly one** of the following four columns is non-null:
> `blocker_defect_id`, `blocker_parts_constraint_id`, `blocker_change_request_id`, `blocker_maintenance_task_id`.
