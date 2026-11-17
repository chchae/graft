# Glide core validation repair

## Fail closed on unmatched or displaced Glide cores (2026-11-17)

**Confirmed user request:** prevent LigPrep states without the selected reference
core, and final poses outside the 1 Å fixed-frame core RMSD, from being admitted
as valid constrained docking results. Preserve existing job identities/results.

**Observed incident:** iteration-8 CMPID_304082 originally matched its group's
72-atom parent SMARTS, but neither prepared LigPrep state matched it. Native logs
explicitly state docking proceeded without comparing to the core position.
CORECONS_FALLBACK False did not prevent this separate nonmatching-core path;
CORE_FILTER defaulted to False. The best-pose reducer checked contact and score
but not core geometry. Its common 17-atom scaffold was 10.05 Å from its actual
parent reference, without alignment. Native, group-best and merged coordinates
were identical. All 19 candidate poses lacked the configured 72-atom core match.

**Repair:** for new submissions, inspect actual LigPrep states against the frozen
reference SMARTS and explicit reference atom order. Record per-state nonmatches
in core_preflight.json; retain original input order/indices so native timeout
subjob ranges remain correct. Write CORE_FILTER True in native inputs so those
states are skipped before docking. Do not alter saved inputs of existing jobs.

Before contact/score reduction, validate every candidate using all native SMARTS
match permutations against the exact frozen reference atom mapping. Calculate
RMSD directly in the receptor coordinate frame, with no alignment or recentering.
Reject missing matches and RMSD above the frozen tolerance (maximum 1 Å). Among
passing poses preserve the existing contact/score/tie policy. Store the measured
r_graft_core_rmsd and core_validation.json rejection diagnostics. Apply the same
validation during normal collection, changed-input recovery and timeout output
collection. Timeout CLI requests must identify the original native core input.

Unknown/ambiguous inputs, missing/invalid reference mappings, malformed geometry
or matcher errors fail closed. Strict collection requires an explicit SMARTS
core and reference atom list; implicit native MCS inputs without that evidence
are held rather than silently assigning a smaller/different core. Standalone
reference-export APIs and explicitly unguarded generic extraction remain
compatible; production stage extractors always require the guard. The CLI also
requires it for old generated headers that lack an explicit policy setting.

Zero accepted poses publish an exact header-only score table and finish ordinary
failure handling, rather than looping on a missing MAEGZ. Retain an earlier group
pose as best_poses.rejected-<identity>.maegz audit, outside merger paths. Extraction
errors preserve earlier published poses. Nonempty scores still require a pose
file. Keep native completion verification, CMPID/state identity, database score
ownership, source fingerprints, saved commands and recovery intact.

**Verification:** full baseline 2,916 passed / 7 skipped; final candidate 2,938
passed / 7 skipped. All existing node outcomes match; 22 new regressions pass.
Installed Schrödinger 2026-1 preflight found 169 nonmatching states among 3,102
inputs for ref_255869. Replay rejected all 19 CMPID_304082 poses through normal
and timeout collection. CMPID_321545 remained accepted at 0.538 Å with unchanged
score; translating that pose 3 Å produced 3.088 Å and was rejected. No new native
calculation was submitted for testing. Tests cover symmetry, threshold boundary,
fixed frame, contact/score precedence, invalid/missing data, archive preservation,
empty-result DB retirement and old generated-header enforcement.

**Existing data is not retroactively certified:** a hash-stable read-only audit of
iteration-8 merged best poses found 554 passing and 127 nonmatching-core poses.
Two rejected IDs, 304745 and 335071, are in the saved MD selection; they had no
persisted MD score when inspected. Keep all old DB/native/manifest/selection data
and jobs unchanged. Their quarantine/recalculation and any affected downstream
ancestry require a separate reviewed recovery operation. The code repair guards
subsequent collection; it does not rewrite previously committed scientific data.


Implementation files: src/graft/glide_pose_selection.py, glide_inputs.py,
glide_timeout_results.py, stages/glide.py and _core.py. Existing test doubles
were extended for preflight reads or given scoped native-core loader stubs;
independent core geometry/integration tests exercise the actual validator.

All 40 source files in the refactoring protected-file inventory remain byte
identical to the task baseline. Existing MD/Prime scientific helper hashes,
Glide saved input files, manifests and production DB are unchanged by this task.
Only new Glide input generation adds CORE_FILTER True. No checksum allowlist
or fingerprint bypass was added.

Raw commands, JUnit XML, before/after logs, source snapshots, native replay and
read-only audit evidence are under /tmp/graft-glide-core-4bd_g90i. The invalid legacy IDs and
input/merged-output hashes are recorded in glide-core-20261117/iter08_invalid_poses.json.

Limitations: no live docking was submitted as a smoke test; recovery of old
implicit-MCS inputs without an explicit mapping is deliberately held. Existing
committed results and frozen downstream selections have not been sanitized.
