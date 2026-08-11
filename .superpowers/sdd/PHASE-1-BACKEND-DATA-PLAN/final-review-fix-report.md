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
