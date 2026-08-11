# NexusTrade Phase 2 — Implementation, deploy and manual QA runbook

## 1. Execution contract

- Branch: `feature/nexustrade-frontend`, based on Phase 1 commit `9b32322`.
- Execution: one Codex session, no subagents, TDD and a local commit per task.
- Integration: HTML, CSS and JavaScript ES modules already used by the application.
- Dependencies: do not add a frontend framework or chart dependency.
- Protected flows: Donchian and Nexus Speed keep their current behavior and UI.
- Safety: `ALLOW_REAL_TRADING=false` and `REAL_MAX_STAKE_USD=0` in every automated,
  localhost and VPS validation performed during Phase 2.

## 2. Frozen backend contract

All normal requests use `X-API-Key`. Governance calls additionally use the transient
`X-Nexus-Human-Key`; that value is never stored in localStorage, sessionStorage,
IndexedDB, URL, logs, WebSocket state or exports.

| Purpose | Method and path |
| --- | --- |
| Hydrate NexusTrade | `GET /api/v1/nexus-trade` |
| Champion mode | `POST /api/v1/nexus-trade/mode` |
| REAL confirmation ticket | `POST /api/v1/nexus-trade/real-confirmation` |
| Emergency stop | `POST /api/v1/nexus-trade/emergency-stop` |
| Versions/campaigns/reports/proposals/exports | `GET /api/v1/nexus-trade/{collection}` |
| Weekly lookup | `GET /api/v1/nexus-trade/reports/weekly/{YYYY-MM-DD}` |
| Report detail | `GET /api/v1/nexus-trade/reports/{report_id}` |
| ZIP/XLSX | `GET /api/v1/nexus-trade/reports/{report_id}/exports/{zip|xlsx}` |
| Approve | `POST /api/v1/nexus-trade/proposals/{proposal_id}/approve` |
| Reanalyze | `POST /api/v1/nexus-trade/proposals/{proposal_id}/reanalyze` |
| Rollback | `POST /api/v1/nexus-trade/rollback` |

The WebSocket uses the existing bot-scoped ticket and bot id `nexus-trade`. Accepted
event types are exactly `nexus.runtime`, `nexus.decision`, `nexus.trade`,
`nexus.campaign`, `nexus.report`, `nexus.trial_changed`, `nexus.proposal` and
`nexus.version_changed`. Reconnect always hydrates a fresh snapshot before actions are
enabled again.

## 3. Visualization contract

The analytical jobs are comparison, time change and operational monitoring. The
primary artifacts are compact metric tables, directly-labelled SVG lines for cumulative
P&L/drawdown and small in-cell bars for differences. Tables are the accessible and
exportable fallback. Essential values never require hover.

- Desktop: operational header, Champion controls, status/progress, tabs and evidence.
- Mobile portrait at 360–430 px: summary and primary evidence first; filters/details in
  disclosures; tables become scrollable or critical-column cards.
- URL state: `nexus_tab`, `week_start`, `report_id`, `campaign_id`, `proposal_id`.
- Transient state excluded from URL: hover, dialog state, credentials and raw payloads.
- Live failure: preserve last-known-good data with `ATUALIZAÇÃO ATRASADA` and timestamp.
- Accessibility: keyboard navigation, visible focus, semantic tables, redundant status
  text, `aria-live`, reduced motion and 44 px primary touch targets on coarse pointers.

## 4. Automated verification

```powershell
node --check static/js/app.js
node --test tests/js/*.test.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q api core database nexus_trade strategies trading tests
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
```

All commands finish with exit code zero. The Git diff for
`strategies/donchian_zigzag.py` and `strategies/nexus_speed.py` remains empty.

## 5. Localhost manual QA

1. Ensure port 8990 is free and use a DEMO-only environment with
   `ALLOW_REAL_TRADING=false`, `REAL_MAX_STAKE_USD=0` and distinct dashboard/human keys.
2. Start with `powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1`.
3. Open `http://127.0.0.1:8990`, authenticate with the dashboard key and select the
   fixed NexusTrade row.
4. Verify OFF/DEMO USD 0.35, ON/DEMO managed stake, emergency stop, one contract per
   lane, `R_100`, M1 and 58 seconds. Do not enable or confirm REAL.
5. Verify daily/weekly/campaign views, Monday 10:00 Brasília selection, immutable old
   report lookup, diffs, gates, recommendations, ZIP and XLSX.
6. Exercise approve/reanalyze/rollback only against seeded DEMO fixtures. Confirm the
   human key is requested, cleared and absent from storage, URL and browser logs.
7. Keep the page open while restarting API and bot. Confirm stale state remains visible,
   reconnect hydrates the snapshot and no F5 is needed.
8. Test keyboard-only use and 1440×900, 1024×768, 768×1024, 390×844 and 360×800.
9. Stop only the owned processes using the launcher cleanup option and confirm port 8990
   is closed.

## 6. VPS deploy

Run from the existing `nexus-trader` directory. Do not use `docker compose down -v`.

```bash
git status --short
git fetch --all --prune
git switch feature/nexustrade-frontend
git pull --ff-only
mkdir -p backups
docker compose stop nexus-bot
docker compose stop nexus-api
docker run --rm -v nexus-trader_nexus-data:/data -v "$PWD/backups:/backup" alpine \
  sh -c 'cd /data && tar czf /backup/nexus-data-before-phase2.tgz .'
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8989/api/v1/health/live
docker compose logs --tail=200 nexus-api
docker compose logs --tail=200 nexus-bot
```

Before `up`, the VPS `.env` contains distinct non-empty values for
`INTERNAL_API_TOKEN`, `DASHBOARD_API_KEY`, `NEXUS_HUMAN_ACTION_KEY` and
`NEXUS_HUMAN_ACTOR`. Keep `ALLOW_REAL_TRADING=false` and `REAL_MAX_STAKE_USD=0` during
the validation window.

## 7. VPS manual QA and rollback

Repeat the localhost functional matrix through the HTTPS domain, including WebSocket
reconnect and downloads. Inspect browser console/network without displaying credentials
in screenshots. A GO requires healthy containers, zero restart loop, zero traceback,
zero secret/account leakage, no REAL attempt and no Donchian/Nexus Speed regression.

If any gate fails:

```bash
docker compose stop nexus-bot nexus-api
git switch --detach 9b3232290c22a0d878e06ad6df3c8db465e57662
docker compose build
docker compose up -d
docker compose ps
```

Restore the volume backup only when a database migration or data-integrity check fails;
restoring it discards data created after the backup. Record the exact reason, timestamp
and retained logs in `PHASE-2-VALIDATION.md`.
