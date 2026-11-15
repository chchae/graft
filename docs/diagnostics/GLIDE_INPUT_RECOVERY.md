# Iteration 6 Glide input mismatch: verified recovery repair

The 2026-11-15 incident was investigated without resetting rows, stopping native
jobs, changing stored checksums or submitting calculations.

## Evidence

- Original iter6 LigPrep JobId: `1619e441-e0ab-11f1-a3e9-ece7a713a41c`, created
  2026-11-15; its completion is in run.log. The frozen input marker was written at
  13:37:24 KST, before the 80 Glide groups were submitted.
- Current source/log instead identifies older completed JobId
  `3d503d59-df6d-11f1-bec3-ece7a713a41c`, created 2026-11-13, with the same launch
  directory. Source/log mtime is 13:38:10 KST.
- The current source contains 25,723 structures / 18,219 unique IDs, range
  64,851–83,069, all outside current iter6 (200,392–250,391). The 80 cached group
  inputs contain 85,470 structures / 48,600 unique IDs, all current iter6 and with
  exact saved per-group counts. The frozen reference map covers original claims.
- Saved SHA256: `d82d21d1d1bc91b5b2266c914fdd3000dd08eb49eb6fbaa7c59e2c8c1a677ab4`.
  Changed source SHA256: `da06de824f5ff71058bbc52137bd9659b6af4563ca4b9f96bbbd20bf588342fb`.
- At diagnosis 7,335 compounds remained running across reference groups 166906,
  172804, 174227, 175887. Native queries showed three parents running and one
  parent/all children done. Existing 2,170 docking scores were not reset.
- The original whole LigPrep output is no longer available on the JobServer.
  Explicit --cwd download and tail attempts only touched owned /tmp diagnostics.
  No checksum substitution or forced job retry was used to accept the wrong source.
- Native input metadata still retains hashes. Candidate validation against each
  actual JobId verified .in, cached ligand group, reference and grid byte-for-byte.
  The exact overwriting process remains unconfirmed; a running downloader alone
  does not identify the writer.

## Repair

Only `src/graft/stages/glide.py` runtime code changes. Before normal fresh-work
logic, a changed existing source/marker enters recovery-only handling. Original
mapping/running ownership and native registered input hashes must agree. New
claims/submissions/splitting/cleanup remain blocked. Missing JobIds/uncertain info
remain pending; timeout records without a native identity and empty mappings fail
closed before completion can be inferred; scientific input mismatch propagates. Legacy timeout reservations
retain their readiness gate. Native input hashes are checked again after terminal
reconciliation/download, before existing result publication/commit code executes.

`prepare_glide_files` is unchanged. Its cleanup/resubmission protection still
rejects changed input with live or uncertain work. The source file and marker are
not repaired, rehashed or replaced; the native group inputs are the authority for
those already submitted jobs. Normal unchanged-input orchestration remains intact.

## Verification

- Before: 80 existing relevant tests passed. The added recovery regression suite
  failed against the original stage, establishing the blocked path.
- After: 198 focused tests passed, including 22 new cases. The first integration
  fixture used the old output basename; it was corrected to the existing saved
  `glide_11.in`→`glide_11_lib.maegz` contract without changing runtime behavior.
- Full suite: **2,904 passed / zero failed / seven skipped** (existing Torch/
  REINVENT4 absence). The 330 existing fork warnings remain.
- All 40 inspected source/native fingerprint inputs are byte-identical. No native
  helper, database schema, transaction implementation or job manifest format was
  changed. Original public function signatures remain. Source/helper logic for
  ordinary collection is unchanged except an optional recovery-only input recheck.
- Actual production input validation was read-only; no native stop/download/submit
  or DB write occurred during that validation. The existing supervisor may resume
  normal verified collection after the atomic source update.

## Limits and follow-up

This repair does not establish who overwrote the source. JobServer launch-directory
reuse and background retrieval should be addressed with separately validated
per-submission input isolation; do not alter/delete existing jobs to do that.
No scientific energy/pose recalculation or output equivalence is claimed. Newly
prepared work remains blocked until its upstream source is valid; this mode only
recovers already submitted, independently verified inputs.

## Observed live recovery after the validated atomic update

The existing supervisor loaded the new stage without a manual worker invocation.
Groups 172804 and 175887 resolved through their existing native jobs. Pending
compounds fell from 7,335 to 3,630; docking scores increased from 2,170 to 2,227,
while normal no-pose terminal failures increased from 40,495 to 44,143. The
remaining groups were 166906 and 174227. New logs show verified recovery/pending,
not the previous input-change traceback. This is a point-in-time observation; it
does not assert that the remaining jobs finished. The global LigPrep marker and
changed source bytes were retained. No native job was manually stopped/restarted
or resubmitted to obtain this observation.

Final validation adds explicit fail-closed coverage for an empty frozen mapping
and a legacy timeout record without a native identity. All 22 new regressions
pass; final full suite: **2,904 passed / zero failed / seven skipped**. The native
input recheck remains before result consumption, and the original cleanup guard
AST and public signatures remain unchanged.
