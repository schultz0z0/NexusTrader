# Phase 1 final-review fix report

Date: 2026-08-11
Branch: `feature/nexustrade-learning`
Base: `2ef3d81201a5e64a47ee7a9fc25f960b61153526`

## Scope and technical verification

Both review findings were reproduced against the base before production code was changed.

1. A trained candidate was not executable by the runtime. Schema-1 artifacts contained only a retraining descriptor, and runtime version transitions changed identifiers while continuing to construct the deterministic V1 strategy. No fitted model or `operate_threshold` was loaded.
2. Runtime pointers and the active Trial campaign were read independently. A fresh repository initialized both lanes from the same Champion V1 identity, and wrong-role pointers or a campaign/version mismatch could be accepted.

The correction preserves the deterministic Bollinger/direction and ADX rules, changes no Donchian/ZigZag/Nexus Speed code, performs no live/network/REAL/Docker operation, and uses no unsafe executable serialization.

## RED evidence

Finding 1 artifact round-trip and validation:

```text
rtk .\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_learning.TrainerTests.test_fitted_artifact_round_trip_matches_sklearn_for_numeric_and_missing_values tests.test_nexus_trade_learning.TrainerTests.test_executable_artifact_rejects_invalid_threshold_and_tampered_fitted_state -v
Ran 2 tests: FAILED (failures=2)
Observed: emitted schema_version was 1 rather than 2, and no fitted_model existed.
```

Finding 1 gate behavior:

```text
rtk .\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_learning.TrainerTests.test_ml_gate_only_blocks_a_deterministic_direction_and_permissive_gate_preserves_it tests.test_nexus_trade_learning.TrainerTests.test_ml_gate_cannot_create_no_trade_bypass_adx_or_operate_on_invalid_features -v
Ran 2 tests: FAILED (failures=2)
Observed: NexusTradeStrategy did not accept or evaluate a gate.
```

Finding 1 transitions and restart:

```text
rtk .\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_runtime.NexusTradeRuntimeTests.test_trial_rotation_and_restart_load_exact_artifact_and_champion_transition_loads_gate tests.test_nexus_trade_runtime.NexusTradeRuntimeTests.test_v1_stays_deterministic_and_corrupt_executable_transition_fails_closed -v
Ran 2 tests: FAILED (failures=2)
Observed: gate remained absent after rotation/restart and corrupt artifacts were not rejected.
```

Unsupported candidate selection also failed before the promotion filter: the weekly replacement test returned `changed=True` for a schema-1 retraining descriptor.

Finding 2 fresh/legacy/corrupt SQLite matrix:

```text
rtk .\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_snapshot_has_versioned_champion_and_active_trial_campaign tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_exact_legacy_v1_pointer_and_campaign_are_migrated_to_a_trial_role tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_snapshot_and_reinitialization_reject_wrong_role_pointers tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_snapshot_and_reinitialization_reject_missing_or_mismatched_trial_campaign -v
Ran 4 tests: FAILED (6 subtest failures)
Observed: fresh Trial had Champion role, exact legacy state was left unchanged, wrong-role pointers were accepted, and missing/mismatched campaign provenance was accepted.
```

Each command was rerun after its minimal implementation and passed. Duplicate active campaigns, malformed identities, unsupported candidates, and the safe Trial lane boundary received additional GREEN coverage.

## Implementation

- Added executable artifact schema 2 with a content-addressed, canonical JSON representation of supported fitted sklearn HistGradientBoostingClassifier state. Strict validation bounds every numeric/state field, rejects malformed/cyclic/unreachable trees and invalid thresholds, and never loads pickle or executable code. Schema-1 descriptors remain readable as legacy metadata but are not executable.
- Added pure-Python HGB inference with explicit missing-value routing and verified probability equivalence to sklearn on supported numeric and missing inputs.
- Added an optional operate-only ML gate to NexusTradeStrategy. It runs only after deterministic direction and ADX validation, cannot create or change CALL/PUT direction, and fails closed on missing, unsupported, or non-finite features.
- Runtime now validates and loads the exact artifact for Trial rotation, Champion promotion, restart, and safe version transitions. V1 remains deterministic. A transition is deferred while its affected lane is non-IDLE and preserves persisted lane state.
- Weekly selection rejects corrupt, legacy descriptor-only, or otherwise non-executable candidates before Trial rotation.
- Fresh SQLite initialization creates distinct Champion V1 and Trial V1 versions. Snapshot and initialization enforce existing pointers with exact roles, exactly one active Trial campaign, and exact campaign-to-Trial-pointer identity. Only the exact known legacy V1 state is migrated; ambiguous or corrupt state fails closed.
- Candidate/version hashes, canonical manifests, artifact rows, roles, and campaign provenance are checked together before a runtime snapshot is accepted.

## Verification evidence

```text
Focused Finding 1 RED->GREEN tests: all pass
Focused Finding 2 repository matrix: 4/4 pass
Duplicate/malformed repository coverage: 2/2 pass
Unsupported candidate promotion coverage: 1/1 pass
Safe Trial boundary runtime coverage: 1/1 pass
Integrated NexusTrade modules: 140/140 pass
Protected Python regressions: 78/78 pass
Full Python discovery: 482/482 pass (66.336s)
JavaScript node tests: 17/17 pass
compileall: pass
pip check: No broken requirements found
git diff --check: pass
Protected diff (Donchian/ZigZag/Nexus Speed): empty
Secret/path scans: pass
```

An earlier full-suite invocation incorrectly exported `DEV_MODE=true` to every test and produced seven settings-test failures whose assertions require production mode. The suite was rerun from a fresh command with `DEV_MODE` removed and the required dummy secrets only; all 482 tests passed without a production change.

The only observed warning is the pre-existing Starlette/FastAPI TestClient deprecation warning. No functional concern remains within this fix scope.

## Final-review fix round 1/5

Base: `f6748bda49f0cdc5939c6863f87b148987dfae0b`

All four re-review findings were verified against production code before their tests or fixes were written.

### RED evidence

1. Active Trial restart with an exact candidate journal:

```text
rtk ... python.exe -m unittest tests.test_nexus_trade_runtime.NexusTradeRuntimeTests.test_restart_active_trial_installs_its_exact_gate_after_safe_settlement -v
Ran 1 test: FAILED (failures=1)
Observed: after the restored ACTIVE position settled to IDLE, the same-version Trial strategy still had gate=None.
```

2. Hash-bound schema-1 Trial approval:

```text
rtk ... python.exe -m unittest tests.test_nexus_trade_promotion.PromotionServiceTests.test_approve_rejects_hash_bound_legacy_trial_before_any_governance_mutation -v
Ran 1 test: FAILED (failures=1)
Observed: PromotionRejected was not raised; a coherent legacy descriptor was approved.
```

3. Post-governance shared-V1 migration:

```text
rtk ... python.exe -m unittest tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_post_governance_legacy_campaign_ids_migrate_and_survive_restart tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_post_governance_legacy_migration_rolls_back_atomically -v
Ran 2 tests: FAILED (failures=3)
Observed: both trial-reanalyze-* and trial-after-* campaign IDs were rejected before migration; the injected rollback path never reached the transactional update.
```

4. WAL reader/writer interleaving:

```text
rtk ... python.exe -m unittest tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_wal_snapshot_stays_on_one_revision_during_atomic_trial_rotation -v
Ran 1 test: FAILED (failures=1)
Observed: the writer committed without blocking, then the reader mixed the old runtime pointer with the new active campaign and raised a provenance mismatch.
```

### Corrections

- Runtime strategy replacement now considers exact gate identity as well as version ID. A missing/mismatched gate on a restored non-IDLE lane queues the validated snapshot; settlement applies it synchronously at the safe IDLE boundary while preserving state and ownership.
- Approval now requires `executable_gate()` and exact candidate ID, artifact hash, canonical envelope, configuration hash, and fitted-state validation inside `_validate_approval`, before any governance mutation. Rejection remains transactionally audited as `REJECTED/ARTIFACT_CORRUPT` with unchanged pointers, proposal, versions, and outbox.
- Shared Champion V1 legacy migration accepts any non-empty unique active Trial campaign ID when campaign/pointer/version provenance is exact and the distinct Trial V1 identity was absent before initialization. This distinguishes genuine legacy databases from fresh wrong-role corruption. Both governance ID families survive restart; injected failure rolls back the new Trial V1, pointer, and campaign update.
- Runtime snapshot reads now use a deferred read transaction with explicit rollback on every `BaseException` (including cancellation), commit on success, and connection-context close. WAL writers remain non-blocking while each reader observes one revision.

The first integrated run exposed one over-broad migration regression: a fresh wrong-role Trial pointer was silently repaired. The migration was restricted using the pre-migration absence of distinct Trial V1; the original corruption test and both valid legacy migrations then passed together.

### Verification

```text
New focused behavior matrix: 6/6 pass
Integrated NexusTrade modules: 156/156 pass
Protected Python regressions: 78/78 pass
Full Python discovery without DEV_MODE: 495/495 pass (70.157s)
JavaScript node tests: 17/17 pass
compileall: pass
pip check: No broken requirements found
git diff --check: pass
Protected diff (Donchian/ZigZag/Nexus Speed): empty
Secret/path scans: pass
```

No network, Deriv call, REAL order, Docker live run, push, PR, or GitHub mutation was performed. The only warning remains the pre-existing Starlette/FastAPI TestClient deprecation warning.
