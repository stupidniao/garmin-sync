# Garmin International <-> China Sync Runbook

Target language: Python

## Goal

Sync Garmin data between:

- Garmin International: `garmin.com`
- Garmin China: `garmin.cn`

Implement in phases:

1. Activity files first.
2. Wellness steps compare and sync eligibility.
3. Additional wellness metrics.
4. Wellness writes only after each metric's write path is verified.

## High-Level Principles

- Use Garmin Connect endpoints through a Python client wrapper.
- Keep International and China sessions separate.
- Prefer one-way sync first: `global_to_cn` or `cn_to_global`.
- Avoid enabling both directions until duplicate handling is proven.
- Use local state to avoid uploading or writing the same data twice.
- Keep state durable enough to resume after partial writes.
- Treat activity sync and wellness sync as separate workflows.
- For activity files, copy the original activity file when possible.
- For wellness metrics, read and compare first; write only when the endpoint or package method is verified.
- Never commit credentials, tokens, downloaded activity files, or local state.

## Local Setup

Use a project-local virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Create the local configuration:

```bash
cp config.example.yml config.yml
```

Then edit `config.yml`:

```yaml
accounts:
  global:
    email: your-global-email@example.com
    password: your-global-password

  cn:
    email: your-cn-email@example.cn
    password: your-cn-password
```

For multiple account pairs, use named profiles and pass `--profile name` to a
command. Legacy `accounts` config is treated as profile `default`.

Every command supports `--audit-log path/to/audit.jsonl`; when omitted, audit
events are appended to `~/.garminsync/profiles/<profile>/audit.jsonl`.

## Python Package

Use `garminconnect`.

```bash
python -m pip install garminconnect
```

Expected client setup:

```python
from garminconnect import Garmin

global_client = Garmin(global_email, global_password, is_cn=False)
cn_client = Garmin(cn_email, cn_password, is_cn=True)
```

Before implementation, inspect the installed package because method names can vary by version.

## Activity Endpoints

International:

```text
GET  https://connectapi.garmin.com/activitylist-service/activities/search/activities
GET  https://connectapi.garmin.com/download-service/files/activity/{activity_id}
POST https://connectapi.garmin.com/upload-service/upload
```

China:

```text
GET  https://connectapi.garmin.cn/activitylist-service/activities/search/activities
GET  https://connectapi.garmin.cn/download-service/files/activity/{activity_id}
POST https://connectapi.garmin.cn/upload-service/upload
```

Activity workflow:

1. List recent activities in source and target.
2. Identify missing source activities.
3. Download original source activity.
4. Extract `.fit`, `.tcx`, or `.gpx`.
5. Upload to target.
6. Verify the upload by response ID or target re-read.
7. Record result locally.

## Wellness Read Endpoints

International base:

```text
https://connectapi.garmin.com
```

China base:

```text
https://connectapi.garmin.cn
```

Common wellness reads:

```text
GET /usersummary-service/usersummary/daily/{display_name}?calendarDate={date}
GET /usersummary-service/stats/steps/daily/{start_date}/{end_date}
GET /wellness-service/wellness/dailyHeartRate/{display_name}?date={date}
GET /wellness-service/wellness/dailySleepData/{display_name}?date={date}
GET /wellness-service/wellness/dailyStress/{date}
GET /wellness-service/wellness/bodyBattery/reports/daily?startDate={date}&endDate={date}
GET /hrv-service/hrv/{date}
```

Wellness workflow:

1. Read source metric by date.
2. Read target metric by date.
3. Compare normalized values.
4. Store local sync state unless this is a dry-run.
5. Add writes only for metrics with verified write support.

Current implemented workflow:

```bash
garmin-sync wellness compare-steps --start 2026-06-11 --end 2026-06-11
```

Optional JSON report:

```bash
garmin-sync wellness compare-steps \
  --direction global_to_cn \
  --start 2026-06-01 \
  --end 2026-06-11 \
  --output reports/steps-2026-06-01-to-2026-06-11.json
```

The command is compare-only. It reads steps with `get_daily_steps(date, date)` on both
accounts, stores hashes and statuses in profile SQLite state,
and performs no Garmin writes.

By default it reads `config.yml`; pass `--config path/to/config.yml` to use a
different local config. Pass `--profile name` to use a named account-pair
profile.

All flows support `--dry-run`. For read-only wellness flows this is an
automation-safe marker recorded in audit logs; for training and activity flows it
prevents Garmin upload/schedule calls. Dry-run never writes local SQLite state.

Steps sync eligibility:

```bash
garmin-sync wellness sync-steps --start 2026-06-10 --end 2026-06-10
```

Rule:

- If CN steps are lower than Global steps, calculate `steps_to_sync`.
- If CN steps are greater than or equal to Global steps, report `sync_disabled`.
- If Global steps are missing, report `sync_disabled`.

Current write limitation: the installed `garminconnect` package has no steps
write method, and authenticated `OPTIONS` for Garmin CN
`/usersummary-service/stats/steps/daily/{date}/{date}` advertises only
`HEAD,GET,OPTIONS`. Until a real write path is verified, the command records
`sync_unavailable` and performs no Garmin write.

## Local State

Runtime state is stored in:

```text
~/.garminsync/profiles/<profile>/state.sqlite3
```

Existing legacy JSONL files under `~/.garminsync` are imported once for the
default profile and left in place as backups. Training sync records uploaded CN
workout IDs before scheduling so retries can resume scheduling instead of
uploading duplicate workout definitions. Activity sync records `synced` only
after the target activity is verified.

Audit events are append-only JSONL and include run start, result rows, optional
report writes, and completion status counts. Do not log passwords, tokens,
downloaded files, raw Garmin payloads, or MFA values.

## Suggested Implementation Order

1. Auth and token storage.
2. Activity one-way sync.
3. Activity historical migration.
4. Wellness steps compare and sync eligibility.
5. Additional wellness read/export.
6. Wellness compare for additional metrics.
7. Wellness writes metric by metric.
