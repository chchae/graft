# Refactoring refinement: baseline, isolation and backend contracts

2026-11-15 KST. Authorized follow-up to the T1–T9 review. Work was prepared in an
isolated copy, using the existing user changes as the baseline. Production DBs,
inputs, manifests, JobIds, native helpers and results were not modified.

## Applied work

1. MD progress/timing tests explicitly select log level 2 and restore their
   ContextVar, keeping every original content assertion. Existing default-level
   quiet and worker-error visibility tests remain unchanged.
2. The self-clash fixture now contains deliberately overlapping, nonlocal atoms
   from different new fragments. The real checker/search still must return
   CLASH_REJECT, retain provenance and the finite-search disclaimer. Only the
   optimizer is held still for this artificial-coordinate test. Production
   embedding, optimization, clash tolerances and molecule identities are unchanged.
3. The timeout test's /proc probe uses one read, treats only disappearance as
   termination, and retains terminal-state/inherited-lock checks. Seven cases
   verify running/sleeping rejection, Z/X, two disappearance errors and propagation
   of other I/O failures.
4. Pytest collection/teardown fallback resources are session-private without an
   external harness. A nested plain-pytest regression checks session ownership,
   per-test paths, child TMPDIR and clean finalization.
5. Seventeen T7 characterization cases cover repeated install/uninstall, the
   process-wide first-worker binding, writer argument/return behavior, Slurm early
   delegation, host-slot order, remote cleanup/errors and a real fork child. These
   run no native/remote calculations. Together with existing tests: 105 passed.
6. Added docs/TESTING.md and PROGRESS.md with current phase status and tuple/dict,
   connection lifetime and callback-capture contracts. Runtime typing/DB/backend
   rewrites were held where they would change compatibility or fingerprint-sensitive
   files for limited benefit. No new framework/schema/global formatter was added.

## Results

| Verification | Result |
|---|---|
| Fresh initial full suite | 2,854 passed / existing 3 failed / 7 skipped |
| Test repairs focused | 257 passed |
| Test repairs full suite | 2,864 passed / 0 failed / 7 skipped |
| Final ordinary pytest, no external plugin | **2,882 passed / 0 failed / 7 skipped** |
| Existing node comparison | Only the three authorized failed→passed repairs; previously passing nodes unchanged |
| New cases | 25 passed: process probe 7 + session isolation 1 + backend contracts 17 |
| Runtime sources | All 114 .py files unchanged from the starting working tree |
| Fingerprint source inputs | All 40 inspected protected files unchanged |

No old failure is hidden by skip/xfail or a weaker assertion. Seven pre-existing
launcher skips remain because Torch/REINVENT4 are unavailable in the accessible
Python environments. The existing 330 fork warnings remain; the multiprocessing
start method was preserved. Mock and synthetic RDKit tests do not establish real
scientific accuracy or live native/multi-host recovery.

The first session-isolation prototype passed its tests but printed a
multiprocessing-finalizer FileNotFoundError after pytest cleanup. The cause was
changing the parent stdlib tempfile cache into a directory removed before its
multiprocessing exit finalizers. The fix gives multiprocessing its separately finalized socket directory before
patching the session tempfile cache, guard paths and exec-child TMPDIR. This also
keeps older test code that temporarily rebinds guard.tempfile away from the shared
submission mutex. Multiprocessing keeps its own directory through interpreter exit. Subsequent targeted checks and the final full log have no traceback.
The initial log is retained; it is not represented as clean verification.
An initially mistyped focused-test path produced exit 4/no tests and is retained
as an unsuccessful command, not counted as validation.

## T7 and remaining architecture

The legacy first-worker singleton and child search mutation are observable and
fork-dependent. Existing code also relies on dispatch installing the stage worker
before supervisor creation. Replacing only the Process target is insufficient.
The new tests improve evidence but do not establish a complete versioned native
migration. No production install/uninstall, fingerprint guard, manifest or DB was
changed. T7 runtime transition and core/DB hook removal remain explicit debt.

## Commit and deployment boundary

The initial Git HEAD and local tree differ in pre-existing features and earlier
refactor follow-ups. Historical patch replay failed at MD/bootstrap contexts.
A smaller test-only candidate passed **2,624 / zero failed / seven skipped**; it
was not the full local program. The user then explicitly requested a GitHub push.
The publication therefore includes a coherent source/test baseline with those
existing feature dependencies in its own commit, followed by the small independent
refinement commits. It excludes target TOML changes, example input deletions,
production outputs/DBs and unrelated metadata. No working user source is reset or
overwritten. The final complete Git candidate is separately exported and verified
against the whole current source/test tree before push.

The whole tested working tree has an isolated wheel and source archive under
`/homes1/chchae/work/GRAFT/graft/.git/refactoring-releases/graft-hardening-4aztmslk`.
[Its manifest](refinement/release-manifest.json) records full source hashes and
artifact SHA256; use those identities, not only package version 0.1.0. This is an
unpublished candidate; no production editable install or remote package/native
helper was replaced. The earlier T3/T4 source mismatch implications still apply
when resuming older saved work. This refinement adds no source hash change.

[Node comparison](refinement/comparison.json), [source audit](refinement/source-audit.json),
[final push-candidate full log](refinement/push-full.log) and small
[patches](refinement/patches/) are retained. [Progress](PROGRESS.md) is the current
T1–T9 entry point; historical reports retain their original baseline.

## Final publication verification

The complete exported Git source/test candidate passed **2,882 / zero failed /
seven skipped** in 84.90 seconds, using ordinary pytest and no external isolation
plugin or custom temporary-root environment. No exit traceback occurred. The
smaller 2,624-case candidate remains an intermediate boundary check, not the
publication result. The complete candidate and local runtime/tests match exactly.

Compileall, package/CLI imports and five CLI help stdout/stderr comparisons passed.
The wheel contains all 114 current Python modules and imports from its isolated
extracted installation. The 533 original inputs were checked before application;
all runtime sources and unrelated user file bytes remain intact. Source/test
changes are the authorized expectation repairs and test-only isolation/contracts.
Existing target example edits/deletions and production artifacts are excluded from
the six-commit publication. No force push or runtime deployment is used.
