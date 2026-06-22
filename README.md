# Garmin Sync

Garmin Sync compares wellness data between Garmin International and Garmin China.

Current v1 scope:

- One-way comparison from Garmin International to Garmin China.
- Steps sync gating from Garmin International to Garmin China.
- No Garmin writes or uploads until a verified steps write path exists.
- Training schedule sync from Garmin International to Garmin China.
- Activity sync from Garmin China to Garmin International.
- Local state records contain hashes and statuses, not credentials or raw payloads.

## Setup

Create and activate a local virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Configuration

Create your local config from the example:

```bash
cp config.example.yml config.yml
```

Edit `config.yml` with your real Garmin credentials:

```yaml
accounts:
  global:
    email: your-global-email@example.com
    password: your-global-password

  cn:
    email: your-cn-email@example.cn
    password: your-cn-password
```

`config.yml` is ignored by git because it contains secrets. Runtime state and
tokens are stored under `~/.garminsync/profiles/default`; International and
China token stores are kept separate under
`~/.garminsync/profiles/default/tokens/global` and
`~/.garminsync/profiles/default/tokens/cn`.

To manage multiple account pairs, use named profiles:

```yaml
profile: default
profiles:
  default:
    accounts:
      global:
        email: your-global-email@example.com
        password: your-global-password
      cn:
        email: your-cn-email@example.cn
        password: your-cn-password
```

Pass `--profile name` to any command to select a configured profile.

All commands write a structured JSONL audit log to
`~/.garminsync/profiles/<profile>/audit.jsonl` by default. Use
`--audit-log path/to/audit.jsonl` to write it somewhere else.

## First Login

The first run authenticates both Garmin accounts and saves tokens to the
configured token stores. Later runs reuse those tokens when Garmin accepts them.

If Garmin requires MFA, the command prompts for the MFA code in the terminal.

## Compare Steps

Compare one day:

```bash
garmin-sync wellness compare-steps --start 2026-06-11 --end 2026-06-11
```

By default the command reads `config.yml`. To use another file:

```bash
garmin-sync wellness compare-steps \
  --config path/to/config.yml \
  --start 2026-06-11 \
  --end 2026-06-11
```

Compare a date range and write a JSON report:

```bash
garmin-sync wellness compare-steps \
  --direction global_to_cn \
  --start 2026-06-01 \
  --end 2026-06-11 \
  --output reports/steps-2026-06-01-to-2026-06-11.json
```

The command prints one row per date:

```text
2026-06-11 steps same source=<hash> target=<hash>
```

Status meanings:

- `same`: Source and target normalized steps payloads match.
- `different`: Both accounts returned data, but the normalized payload hashes differ.
- `missing_source`: China has data for the date but International returned no steps payload.
- `missing_target`: International has data for the date but China returned no steps payload.
- `read_error`: At least one account failed while reading the steps payload.

`compare-steps --dry-run` is accepted for automation consistency. The flow is
already Garmin read-only; dry-run is recorded in the audit log and no Garmin
write methods are called.

## Sync Steps

Evaluate whether steps should sync from Global to CN:

```bash
garmin-sync wellness sync-steps --start 2026-06-10 --end 2026-06-10
```

Preview mode:

```bash
garmin-sync wellness sync-steps --dry-run --start 2026-06-10 --end 2026-06-10
```

Sync rule:

- If CN steps are lower than Global steps, the command calculates `steps_to_sync`.
- If CN steps are greater than or equal to Global steps, sync is disabled.
- If Global steps are missing, sync is disabled.

Current write limitation:

Garmin's daily steps endpoint currently advertises only `HEAD`, `GET`, and
`OPTIONS` for `/usersummary-service/stats/steps/daily/{date}/{date}`, and the
installed `garminconnect` package does not expose a steps write method. Because
there is no verified Garmin write path, `sync-steps` reports
`sync_unavailable` instead of pretending to sync.

## Sync Training Schedule

Sync planned workouts from Global to CN:

```bash
garmin-sync training sync-schedule
```

By default, the command syncs from today through the same calendar day next
month. For example, on 2026-06-11 the default range is 2026-06-11 through
2026-07-11.

Preview the actions without uploading or scheduling anything in CN:

```bash
garmin-sync training sync-schedule --dry-run
```

Use an explicit range:

```bash
garmin-sync training sync-schedule \
  --start 2026-06-11 \
  --end 2026-07-11
```

The workflow:

- Reads Global scheduled workouts from Garmin Calendar.
- Fetches each Global workout definition.
- Removes Global account-specific IDs and timestamps from the workout JSON.
- Skips workouts already synced in local state.
- Skips a workout when CN already has a same-name workout on the same date.
- Uploads the normalized workout to CN.
- Schedules the new CN workout on the same date.
- If upload succeeds but scheduling fails, records the uploaded CN workout ID
  and resumes scheduling from that ID on the next run instead of uploading a
  duplicate workout.

Use `--force` to bypass the local-state and same-day same-name duplicate checks:

```bash
garmin-sync training sync-schedule --force
```

Status meanings:

- `dry_run`: Would upload and schedule the workout, but `--dry-run` prevented writes.
- `uploaded`: Internal durable state recorded after upload succeeds and before scheduling.
- `synced`: Workout was uploaded to CN and scheduled on the matching date.
- `skipped_state`: The same date and workout hash were already synced before.
- `skipped_existing`: CN already has a same-name scheduled workout on that date.
- `read_error`: A schedule or workout read failed.
- `upload_error`: Uploading the workout definition to CN failed.
- `schedule_error`: Upload succeeded, but scheduling the uploaded workout failed.

## Sync Today's Activity

Sync today's activities from CN to Global:

```bash
garmin-sync activity sync-today
```

Preview without downloading or uploading:

```bash
garmin-sync activity sync-today --dry-run
```

Sync an explicit date:

```bash
garmin-sync activity sync-today --date 2026-06-11
```

The workflow:

- Reads CN activities for the selected date.
- Reads Global activities for the same date.
- Skips an activity when Global already has the same start time and name.
- Skips source activities already synced in local state.
- Downloads the CN original activity ZIP.
- Extracts the original FIT file.
- Uploads that FIT file to Global.
- Records `synced` only when Garmin returns a target activity ID or a target
  re-read verifies the uploaded activity.

Use `--force` to bypass the local-state and duplicate checks:

```bash
garmin-sync activity sync-today --force
```

Status meanings:

- `dry_run`: Would download and upload the activity, but `--dry-run` prevented writes.
- `synced`: Activity was downloaded from CN and uploaded to Global.
- `no_source_activity`: CN has no activities on the selected date.
- `skipped_state`: The source activity was already synced before.
- `skipped_existing`: Global already has a same-start-time same-name activity.
- `read_error`: Reading source or target activities failed.
- `sync_error`: Download, FIT extraction, or upload failed.

## Local State

State is stored in SQLite:

```text
~/.garminsync/profiles/<profile>/state.sqlite3
```

On first run for the default profile, existing legacy JSONL state files under
`~/.garminsync` are imported into SQLite once and left in place as backups.
State records include the run timestamp, direction, metric/date identifiers,
status, source/target IDs or hashes, and a short error string when a read or
sync action fails.

Audit logs are append-only JSONL files:

```text
~/.garminsync/profiles/<profile>/audit.jsonl
```

Each run logs `run_started`, one `result` event per result row, optional
`report_written`, and `run_completed` with status counts. Audit rows include the
profile, command, run ID, direction, dry-run flag, date range, state path, and
sanitized result fields. They do not include credentials or raw Garmin payloads.

## Safety

Steps commands only call `get_daily_steps(date, date)` on each Garmin account.
They do not call Garmin write methods, upload endpoints, delete endpoints, or
activity sync paths.

Training schedule sync calls Garmin write methods only when `--dry-run` is not
set. It uploads new CN workout definitions and schedules them on matching dates.
It records upload progress before scheduling so retries can resume safely. It
does not delete or overwrite CN workouts.

Activity sync calls Garmin write methods only when `--dry-run` is not set. It
uploads extracted FIT activity files to Global. It does not delete or overwrite
Global activities. Ambiguous upload responses are treated as `sync_error` unless
the target account confirms the uploaded activity.
