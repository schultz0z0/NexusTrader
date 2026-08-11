# SDD ledger — plan: docs/NexusTrade-Learning/PHASE-1-BACKEND-DATA-PLAN.md

Workspace: `.worktrees/nexustrade-learning`
Branch: feature/nexustrade-learning
Started: 2026-08-10

Pre-flight: clean plan scan; no conflicts found.
Baseline: 190 tests passed with required dummy settings and isolated `.venv`.
Baseline warning (pre-existing): StarletteDeprecationWarning from FastAPI TestClient/httpx.
Execution constraint: local commits only; never push, open PR, or mutate GitHub state.
Task 1: complete (commits 11da778..23529a4, review approved).
Task 1 deferred minor: clarify in docs/DEVELOPMENT.md that stake and entry delay are immutable while NEXUS_DAILY_CLOSE_HOUR is range-validated (0..23).
Task 2: fix round 1/5 (4 addressed, 2 open — Champion V1 corruption handling; journal integrity guards; commits 4e7cde7..b2d75ec).
Task 2: fix round 2/5 (2 addressed, 1 open — complete behavioral coverage for journal INSERT/UPDATE guards; commits b2d75ec..40cda3b).
Task 2: fix round 3/5 (1 addressed, 0 open — complete journal guard matrix covered; commits 40cda3b..c7d6d57).
Task 2: complete (commits 23529a4..c7d6d57, review clean).
Task 3: minor (deferred): IndicatorFrame is frozen but exposes mutable dict values.
Task 3: minor (deferred): FeatureBuilder internal zip should assert equal lengths and aligned epochs.
Task 3: fix round 1/5 (6 addressed, 4 open — intrabucket candle cutoff; populated legacy sequence migration; legacy orphan recovery; single-writer enforcement; commits 09eb65c..512f94b).
Task 3: fix round 2/5 (4 addressed, 1 open — cross-process lock/release and migrated unique-constraint behavioral proof; commits 512f94b..acba84b).
Task 3: fix round 3/5 (2 addressed, 1 open — release tests must keep holder process alive after context exit/initialization error; commits acba84b..6a16e7f).
Task 3: fix round 4/5 (1 addressed, 0 open — lock release proven while holder process remains alive; commits 6a16e7f..ad52965).
Task 3: complete (commits c7d6d57..ad52965, review clean; 2 deferred minors).
Task 4: fix round 1/5 (8 addressed, 3 open — causal cycle proof; numeric Deriv contract_id; owner-checked quarantine reconciliation; commits 5f9faff..ad76775).
Task 4: minor (included in next fix): reject ADX outside valid domain before lane reservation.
Task 4: fix round 2/5 (3 addressed, 1 open — persist expected quarantine correlation and validate it on first reconciliation; commits ad76775..63c5f73).
Task 4: fix round 3/5 (1 addressed, 0 open — quarantine correlation bound before first reconciliation; commits 63c5f73..0879264).
Task 4: complete (commits ad52965..0879264, review clean).
Cross-task regression: tests.test_order_ownership has 7 FK fixture errors after Task 2 persistence hardening; must be resolved before Task 5.
Cross-task regression: resolved (commit 488fd0f, review clean; full suite 276/276).
Task 5: initial implementation commit 24571a7; review CHANGES_REQUIRED (4 Critical, 3 Important).
Task 5: fix round 1/5 PAUSED by user before final suite/commit. Implementer reported C1-C4 and I1-I3 GREEN; focused matrix was 38/39 with the sole fixture error then corrected; final full/JS suite was running when interrupted.
Task 5 paused dirty files: database/repository.py, nexus_trade/dispatcher.py, nexus_trade/runtime.py, trading/ownership.py, tests/test_nexus_trade_dispatcher.py, tests/test_nexus_trade_runtime.py.
Resume action: resume /root/phase1_task5_impl, rerun focused + full Python + JS + compileall/pip/diff, append task-5-report, commit fix round 1, generate review package from 24571a7 to new HEAD, dispatch fresh scoped re-review.
Task 5: fix round 1/5 (3 addressed, 4 open — owner account identity across restart; CancelledError/pre-run stop; ownership metadata preservation; same-identity dispatcher instance swap; commits 24571a7..d26e849).
Task 5: minor (included in next fix): reconciler must reject string/bool/non-positive contract_id without coercion.
Task 5: fix round 2/5 (4 addressed, 3 open — post-transport reservation before quarantine write; settlement clears latest lane snapshot; quarantine persistence strictly bounded; commits d26e849..3ffaf32).
Task 5: fix round 3/5 (2 addressed, 1 open — concurrent process_cycle can overwrite settlement IDLE snapshot with ACTIVE; commits 3ffaf32..0267407).
Task 5: fix round 4/5 (2 addressed, 0 open — lane settlement serialized; indexed canonical lane head/ledger replaces JSON scans; commits 0267407..ece8130).
Task 5: complete (commits 488fd0f..ece8130, review clean).
Task 6: initial implementation commit 22437b6; review CHANGES_REQUIRED (0 Critical, 3 Important: persisted emergency ordering, realtime decision/trade publication, strict idempotent sanitized event envelopes).
Task 6: fix round 1/5 (3 addressed, 0 open; commit 193b3bd).
Task 6: complete (commits ece8130..193b3bd, review clean).
Task 7: initial implementation commit 060322a; review CHANGES_REQUIRED (1 Critical: loss payout=0 rejected; 2 Important: empty artifact can become Trial; legacy candidate status can mutate outside SHADOW/TRIAL).
Task 7: fix round 1/5 (3 addressed, 0 open; commit cca9b0c).
Task 7: complete (commits 193b3bd..cca9b0c, review clean).
Task 8: initial implementation commit c30141a; review CHANGES_REQUIRED (1 Critical: persisted weekly path bypasses gate evaluation/trusts caller recommendation; 3 Important: daily midnight buckets, missing provenance accepted, indicator addition defaults ablation PASS).
Task 8: fix round 1/5 implemented and verified (4 addressed, 0 open — internal persisted gate evaluation, 10:00 São Paulo business windows, exact provenance identity, explicit indicator ablation; focused 49/49, regressions 199/199, full Python 398/398, JavaScript 17/17; local commit `fix: govern NexusTrade report evidence`).
Task 7: initial implementation committed locally as 060322a; focused 13/13, protected regressions 158/158, full Python 370/370, JavaScript 17/17, compileall/pip/diff/protected checks clean. Awaiting review.
Task 7: fix round 1/5 committed locally as cca9b0c; 3 review findings addressed, focused 17/17, protected regressions 158/158, full Python 374/374, JavaScript 17/17 and static/protected checks clean. Awaiting re-review.
Task 9: initial implementation verified — human-only approve/rollback with CAS and transactional safety barriers; exact immutable evidence/gates; reanalysis preserving Champion/history; Monday 10:00 Brasília Trial replacement; deterministic post-commit outbox, sanitized attempt audit, thin authenticated API, fault/restart/legacy coverage. Focused 21/21, protected 78/78, full Python 419/419, JavaScript 17/17, compileall/pip/diff checks clean. Awaiting review.
Task 9: fix round 1/5 implemented and verified (1 Critical + 5 Important addressed — exclusive human credential/server actor, strict real lane state, atomic candidate role transition, unconditional REANALYZE confirmation, corrupt-attempt audit, exact indexed outbox request identity/migration; focused 30/30, integrated 147/147, protected 78/78, full Python 428/428, JavaScript 17/17 and checks clean). Awaiting re-review.
Task 9: fix round 2/5 implemented and verified (1 Important addressed — human governance key now rejects collision with dashboard/internal/Deriv authorities in production and configured DEV, with secret-free errors; focused 45/45, integrated 151/151, protected 78/78, full Python 432/432, JavaScript 17/17 and checks clean). Awaiting re-review.
Task 9: fix round 3/5 implemented and verified (1 Important addressed — settings post-load contracts now raise sanitized non-Pydantic structured errors, redacting str/repr/errors/json while preserving sources/parsing/policies; focused 45/45, integrated 151/151, protected 78/78, full Python 432/432, JavaScript 17/17 and checks clean). Awaiting re-review.
Phase 1 final-review fix wave: both Important findings addressed — executable content-addressed JSON HGB operate-only gate loaded across safe runtime transitions/restart; strict pointer-role and active Trial campaign provenance with exact legacy migration/fail-closed SQLite handling. TDD RED evidence recorded; integrated 140/140, protected 78/78, full Python 482/482, JavaScript 17/17, compileall/pip/diff/protected/secret/path checks clean. Local commit only; no network/live/REAL/Docker/push/PR/GitHub mutation.
Phase 1 final-review fix round 1/5: 4 Important findings addressed — same-version active restart installs the exact gate at safe settlement; approval rejects/audits non-executable or non-canonical Trial artifacts before mutation; unambiguous shared-V1 trial-reanalyze/trial-after campaigns migrate atomically while fresh corruption remains fail-closed; WAL snapshots use one deferred read transaction with error/cancel cleanup. REDs recorded; focused 6/6, integrated 156/156, protected 78/78, full Python 495/495, JavaScript 17/17 and static/protected/scans clean. Local commit only.
Phase 1 final-review fix round 2/5: ambiguous shared-V1 compatibility migration now accepts only genuine initial/trial-reanalyze/trial-after identities and refuses migration when another validated TRIAL version exists; atomic rollback and approved legacy/restart paths preserved. RED recorded; focused 4/4, repository 31/31, integrated 293/293, protected 78/78, full Python 496/496, JavaScript 17/17 and static/protected/scans clean. Local commit only.
