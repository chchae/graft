# Graft Development Handoff

## Release NFS controller files before recursive cleanup (2026-11-08)

**Observed bug:** iter09 MD result collection stopped at remote cleanup with
`OSError: [Errno 39] Directory not empty: replica01`. The adapter removed a
compound tree while its own controller.lock descriptor was still open. On NFS
the unlink created a temporary .nfs file, preventing directory removal until
the adapter exited. The complete-run cleanup held cleanup-run.lock similarly.

**Repair:** split retirement from recursive deletion. Persist the exact cleanup
receipt and atomically rename the owned tree to its tombstone while holding
controller locks; close every lock through ExitStack, then recursively remove
the retired tree. Apply this ordering to both compound and complete MD runs.
Replay still drains an existing tombstone from its durable receipt. Preserve
all score/archive/native identity checks, symlink isolation and cleanup gates;
do not suppress ENOTEMPTY or discard a pending cleanup acknowledgment. No
scientific helper, protocol, JobId, score or active MD job is reset.

**Verification/outcome:** 201 cleanup/MD/retry/reference regressions passed.
New tests prove locks are held during retirement and released during removal.
A tiny disposable folder on the actual Slurm NFS share reproduced errno 39
and an open-file .nfs entry; the corrected rename/close/delete sequence and
its replay succeeded there. These probes contained only dummy lock files and
were removed. Compileall, package/CLI imports and diff checks passed.
The existing supervisor recovered automatically: iter09 remote cleanup pending
count and remaining compound tombstones both reached zero. Its 12 already
committed scores/stds/snapshot counts remained exact. Other MD work continued;
no new scientific calculation or binding-accuracy validation was performed.


## Put new Slurm GBSA work under project output (2026-11-08)

**Confirmed user request:** stop creating Amber Slurm and MD-GBSA work roots
directly under the Slurm user's home; create them below the project's `output`
and delete collected GBSA working trees after calculations finish.

**Implementation:** new default Amber Slurm work is
`<out-dir>/graft-amber-slurm-runs/run-<uuid>` and new MD work is
`<out-dir>/graft-md-gbsa-runs/<uuid>`. Resolve the project's local `out-dir`
relative to its TOML, independent of invocation directory, then use its absolute
shared-filesystem path on Slurm. Pass the TOML base into MD settings validation.
Explicit absolute remote-work-dir overrides remain remote, with no client-side
normalization. Prime shared-compute paths and scientific settings remain exact.

Saved MD selections retain their frozen roots. Saved Amber SSH, single/array
and legacy shard work continues using its saved directory when only the new
work-directory setting changes; all other saved host/environment/protocol/CPU
checks remain enforced. Never move a live run, edit its JobId or resubmit work
to apply a new default. The existing post-commit cleanup removes successful
owned run trees in the new output location, retaining the project DB, final
results, logs/archives and small restart receipts. Active/failed/uncertain work
is retained. Shared parent containers remain under output. No historical home
folder sweep or live job relocation is authorized by a default-path change.


**Verification:** 515 affected configuration, Amber recovery, MD selection
and post-commit cleanup regressions passed. Full suite: 2,097 passed, seven
skipped and the previously documented unrelated real-fragment collision test
failure in `tests/test_combichem_3d.py`. Tests cover TOML-relative/absolute output,
invocation-directory independence, target isolation, explicit remote overrides,
saved SSH/Slurm single/array/shard paths and frozen MD roots. Temporary output
trees verify successful run deletion while preserving final scores and neighbors.
Editable installation, compileall, package/CLI imports and diff checks passed.

The current EGFR TOML resolves both defaults under
`examples/egfr-crem-pipeline/output`; read-only SSH confirmed that Slurm sees the
same writable output path. No production database, saved JobId, active run path
or historical home directory was moved/deleted, and no scientific job was
submitted. Native calculation accuracy/destructive remote cleanup was not
validated by these checks.


## Remove fully collected calculation run directories (2026-11-08)

**Confirmed user request:** automatically remove intermediate calculation
folders such as `graft-amber-slurm-runs` and `graft-md-gbsa-runs` after extracting
the results. Apply deletion to owned per-run trees after durable result
collection; preserve shared parent containers, project results and recovery
receipts. Failed, active or uncertain calculations retain their working files.

**Implementation:** retain the existing Amber Slurm/SSH post-commit full-run
cleanup and successful-compound pruning for partial runs, plus native Prime
shared-tree collection/cleanup. Extend MD cleanup from compound trajectories to
the entire UUID run, including helpers, inputs and control files, once every
selected score is finite and every individual remote/local collection is
acknowledged. This supersedes the earlier policy of retaining a successful MD
run's helper/input directory. Historical scores without individual cleanup
acknowledgments do not enroll old runs for automatic deletion.

Before deleting an MD run, archive its remaining files under the iteration's
`09-md_gbsa/remote-cleanup/run-evidence.tar`; verify transport checksum and that
the working files still match the archive. Require exact frozen selection,
compound cleanup receipts, terminal DONE native parents/children, no leftover
compound/foreign work and no symlinks. Save the run cleanup intent in SQLite
using an atomic recheck of all score/collection commits. Stage completion and
advancement wait for acknowledgment of an enrolled intent. A durable remote
receipt and tombstone rename recover interrupted removal or lost SSH replies
without resubmission. Existing scientific helpers/protocols remain immutable;
cleanup is an independent operational adapter. Unregister resolved remote
owners and finish the existing local Prime run cleanup afterward.


**Verification:** 455 affected Amber/MD/Prime/native-query regressions passed;
190 focused cleanup/MD/reference-extension tests passed after the final cleanup
acknowledgment ordering change. A broader suite run reported 2,084 passed, seven
skipped and the previously documented unrelated real-fragment collision test
failure in `tests/test_combichem_3d.py`. Tests use real temporary files/SQLite
and mocked native status/transport, covering uncommitted/partial results, live
children, neighbor/symlink isolation, changed archives, interrupted tombstones
and lost deletion replies. Editable installation, compileall, package/CLI
imports and diff checks passed. No production sweep, submitted-job cancellation
or remote destructive smoke test was performed. Existing on-disk scientific
helpers and live/uncertain work were preserved.


## Resume native recovery after removal of historical MD roots (2026-11-08)

**Observed failure:** iter09 supervision stopped at an already completed LigPrep
stage because the global native query invoked a removed historical MD
`native.py`. Both registered Slurm MD roots were absent; registrations and
remote suites remained valid. SQLite already had 13 Glide scores and 1,111
glide_running compounds. No LigPrep resubmission or compound reset was needed.

**Implementation repair:** for remote MD `jobs` inspection only, stream the
current standalone native query through the registered Suite's `run python3 -`
instead of loading a run-owned script. This reads native parents/children and
any surviving uncertain Prime journals; it creates no run directory, launches no
scientific job and does not edit frozen helpers or the remote registry. Native
lookup/SSH failures still block submission. Local Prime retains its existing
legacy JobControl adapter and journal checks. Scientific actions still invoke
their original frozen helper. Also scope a stage's live-job completion check to
its own stage directory: a live Glide job must not prevent a restarted supervisor
from passing completed LigPrep and reaching the Glide recovery worker. Saved
launch intents/queues remain checked; cross-project submission exclusion remains.

**Verification/outcome:** 255 remote-query, submission-fence, MD, legacy Prime,
sequential-supervision, output-path and Glide-recovery regressions passed. Tests
cover missing roots, live children, surviving uncertain journals, fail-closed
queries and completed-LigPrep/live-Glide restart. Compileall, imports and diff
checks passed. Real read-only queries through the registered suites succeeded;
Glide remained visible and LigPrep completion returned clear. Gracefully
interrupted only the local supervisor; the user's existing retry loop started
a new supervisor and iter09 Glide worker. Existing native jobs, DB states, scores,
registrations and files were retained. Native scoring completion is not claimed.


## Explicit deleted iter09 regeneration (2026-11-08)

**Observed evidence and confirmed user request:** current EGFR `output` lost all
1,127 iter09 compound rows and its directory while frozen CReM/priority/pose
metadata remained. The population guard correctly blocked restart. The user
requested regeneration and explicitly authorized stopping remaining iter09 Glide
jobs. Native inspection found two live parents and their remaining children.

**Operational recovery:** verified native `jsc stop` options, stopped only the
two iter09 parents, confirmed both parents and all 100 children terminal, and
downloaded available outputs into
`output/.previous/iter09-user-deleted-1794099988880631867/`. Under driver/all-stage
locks, backed up SQLite/WAL, tested the existing explicit deleted-CReM reset on
an isolated copy, then removed only orphan iter09 metadata, including its priority
marker. The full population guard remains unchanged. Iter0–8 compound checksum
was unchanged before/after reset and regeneration; integrity/FK checks passed.

Ran the independent generation worker from the same eight iter8 MD-ranked
parents: 1,925 attempts, 1,127 unique accepted structures, stop=exhausted, saved
CReM state=done. This exhausted the available search below target-count=10,000;
no target was changed or population completion fabricated. Independent druglike
assessment admitted all 1,127 with processing priorities. Driver selection now
returns 9 and LigPrep readiness is clear. No new LigPrep/Glide job was submitted.

**Verification:** 93 CReM/workspace/priority regressions passed, including backup
preservation and removal of the priority marker during explicit deleted-iteration
reset. Compileall, imports and diff checks passed. Real generation completed;
this is not validation of binding affinity or subsequent docking/GBSA results.


## Drug likeness as processing priority (2026-11-08, latest requirement)

**Confirmed user request:** use drug likeness to judge priority instead of
filtering compounds. This supersedes the RO5<=1 gate, half-population QED cutoff
and optional SA fraction for newly assessed raw-generation iterations.

**Implementation choice:** assess the full frozen population; admit every
molecule and order preparation by RO5 violation count ascending, QED descending,
SA ascending and compound ID ascending. Glide reference groups enter the existing
parallel scheduler in the order of their best pending molecule, retaining group
membership, native batching/recovery and immediate result persistence. This is
submission/input ordering, not a guarantee of native completion order.
Drug likeness does not replace Glide/GBSA top-N ranking or MD-ranked parents.
`sa-accepted-ratio` remains a legacy compatibility option, ignored by priority
assessment. Descriptor calculation failures still block admission.

Persist an iteration priority marker alongside the existing complete population
assessment; all members have passed=1 and no rejection reason. Reports append a
priority column. Saved historical filters and legacy workspaces remain frozen.
Explicit conversion of an entirely unstarted latest raw iteration requires caller-
held driver/all-stage locks, a SQLite backup and no saved external jobs. In one
transaction archive the previous selection, restore only druglike_rejected rows
to generated, and admit the full population. Refuse failures, results, downstream
selections and later saved work; never reset scientific work or mutate older
iterations. Normal restart does not automatically reopen historical rejections.


**Verification:** 183 affected priority, concurrency, restart, workspace, CReM,
SA and Glide regressions passed after the final changes. A broader suite run
reported 2,066 passed, seven skipped and the previously documented unrelated
`test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself`
failure. Editable installation, compileall, package/CLI imports and diff checks
passed. These tests do not validate native scientific calculations.

**Operational application:** the current EGFR pipeline TOML points to `output`,
whose latest iter09 has 1,127 compounds; the earlier inspected `output-00` has
140 and remains untouched. Held driver/all-stage locks, verified no downstream
files, backed up SQLite/WAL and the old report under
`output/.previous/iter09-druglike-priority-1794098463403319068/`, and first tested
the conversion on an isolated DB copy. Converted only current iter09's 1,127
rejections to generated, archived old decisions/timing in SQLite and regenerated
the report with priority. Earlier compound rows matched their pre-conversion
checksum; integrity/foreign-key checks passed and LigPrep readiness is clear.
No external calculation was launched.


## Automatic cleanup after committed Amber / MD-GBSA results (2026-11-07)

**Confirmed user request:** automatically remove used Amber work and MD
trajectories after GBSA scoring finishes. This supersedes the earlier retention
policy for successfully scored work only; failed/active/uncertain work remains.

**Implementation:** independent workers collect results before deletion. Amber
requires committed matching finite scores, archived calculation logs, immutable
input identity, terminal Slurm parent/tasks where applicable, and released
inherited task locks. Fully successful runs are removed; partial runs remove only
successful task scratch/ligand trees while retaining failure inputs and completion
markers required by explicit retry. Keep the active local manifest until cleanup
is acknowledged. Durable per-run intents/receipts and tombstone renames recover
lost SSH replies or interrupted removal without relaunching scientific jobs.

MD records pending remote cleanup in SQLite in the score transaction when native
MD evidence is available. Completion/next-iteration gates wait for acknowledgment.
After local Prime collection/cleanup, archive MD inputs, frame metadata, logs,
native journals and final system coordinates under `09-md_gbsa/remote-cleanup/`;
verify the archive checksum before removing the owned remote compound tree,
including its trajectories/checkpoints. Verify the original native parent and
children are DONE and controller locks are released. Keep run-level MD selection,
helpers and small cleanup receipts; failed compound trajectories are retained.
Existing historical scores are not retroactively enrolled or recomputed. No
protocol, frame selection, score, JobId or scientific retry policy is changed.

**Verification:** 721 relevant workflow/DB/cleanup regressions passed (temporary
SQLite/files with mocked native status/transport), including active/unknown job
refusal, partial failure preservation, neighbor/symlink isolation, archive checksum
checks, commit-before-delete ordering, interrupted deletion and lost-reply
recovery. Compileall, package/CLI imports and diff checks passed. No real scientific
accuracy or remote destructive smoke test is claimed.

## Project-specific submission client override (2026-11-06)

**Confirmed user request:** explicitly use a compatible 2026-1 submission client
for the EGFR CReM pipeline after LigPrep was blocked by Slurm JobServer API
1.19.4 versus the local default client's 1.21.2.

**Implementation:** optional top-level `schrodinger` selects the project's local
submission installation; relative values resolve against the TOML directory.
Explicit caller/CLI override takes precedence, followed by TOML, then the existing
SCHRODINGER environment/default fallback. Reject empty/non-string values. Set
examples/egfr-crem-pipeline/egfr.toml to `/share/opt/schrodinger/2026-1`, which is
also accessible locally. This project-specific request supersedes the default
alias preference only for its submission client. Keep remote MD and local Prime
installations, frozen settings, scientific parameters and saved jobs unchanged.

**Verification:** 265 configuration, scaffold, driver and submission-guard tests
passed; compileall, package/CLI imports and diff checks passed. Read-only native
preflights passed for LigPrep and Glide with matching API 1.19.4. The existing
supervisor independently read the updated TOML and submitted iteration 1's 573
LigPrep compounds with JobId faa5b904-d9d4-11f1-8da5-ece7a713a41c. Submission is
not evidence of scientific completion; no server upgrade or database reset was
performed.

## Use default aliases for 2026-1/2026-3 execution paths (2026-11-06)

**Confirmed user request:** replace Schrödinger 2026-1 and 2026-3 execution paths
in source and TOML files with `default`. This supersedes the earlier 2026-3 pin
for configuration of new work; it does not reinterpret historical run evidence.

**Implementation:** use `/home1/schrodinger/default` for the shared local stage
and 3D input-extraction defaults, and `/share/opt/schrodinger/default` for remote
MD. Update the EGFR CReM pipeline and both version-pinned EGFR combichem TOMLs,
plus the 3D settings example's local override comment. Retain CLI/environment
precedence, explicit alternative overrides, local path normalization and remote
path preservation. Tests use named synthetic saved installations to continue
checking frozen-selection preservation and explicit changed-installation recovery.
Preserve unrelated installation versions, scientific options and the 2026-3
LigPrep option warning; no external options are changed.

**Verification:** 430 configuration, MD, 3D and integrated CReM regressions passed,
with the previously documented real-fragment collision assertion explicitly
excluded. All 15 tracked TOMLs parse; no requested version-pinned executable
paths remain in production source or tracked TOMLs. Compileall, package/CLI
imports and diff checks passed. No production DB, saved JobId, frozen remote
settings/helper or machine-level default symlink was changed, and no native job
was launched. These alias edits do not establish that the supervisor startup
failure is fixed; saved runs still require their explicit recovery procedure.

## Block iteration advancement when required MD has no finite scores (2026-11-06)

**Observed user report:** iteration 1 began while SQLite had no reference
MD-GBSA values. Read-only inspection found 24 reference MD failures, all
JOB_SUPERVISOR_NOT_STARTED, with exhausted automatic retries and zero finite
scores. No calculated ensemble awaited DB import. All failed native attempts
were correctly considered resolved, but that status incorrectly admitted growth.
The user clarified that the score gate must follow the TOML MD enable option;
a saved historical selection alone does not make finite scores mandatory.

**Implementation repair / minimum eligibility choice:** preserve the existing
MD completion API's distinction between pending work and resolved terminal
failures. Separately require at least one finite MD score before reference or
later-iteration advancement only when TOML `md-gbsa=true`.
This minimum prevents empty-score advancement; it does not require every selected
MD to succeed or change elite-count/ranking/training gates. Pending replicas and
cleanup still block even when one score exists. With `md-gbsa=false`, missing
scores add no advancement gate, even for resolved historical selections. Saved
pending jobs, retries and cleanup still require recovery regardless of the toggle.

The driver selects the blocked preceding iteration rather than a new one. After
an enabled MD worker resolves all failures without a usable score, pause supervision once
with an explicit advancement blocker, without exporting FEP, publishing stage
completion or granting new retries. The independent CReM worker applies the same
option-controlled score gate before native input preparation. CReM's separate
MD-ranked parent/provenance rule remains unchanged, with no score fallback.
Keep completed/frozen generated populations and all scores/JobIds unchanged;
never fabricate scores, substitute Prime/Glide parents or reset failed jobs.

**Verification:** 230 MD/CReM/retry/driver/logging/progress regressions passed.
New tests cover zero-score failures at iterations 0/1, partial success, one-call
supervisor pause, enabled/disabled MD with resolved saved selections, disabled
MD with pending saved work and early standalone CReM admission checks.
Compileall, package/CLI imports and diff checks passed. The user's retry loop and
local supervisor were stopped gracefully. Read-only inspection of the actual DB
now selects iteration 0 and blocks bootstrap generation, retaining the already
created iteration-1 population. Native infrastructure failure remains unresolved;
no scientific calculation, score import, DB reset or new submission was performed.
A subsequent read-only disabled-option probe encountered an independent frozen
iter1 population mismatch (expected 1146, actual 0). That corruption guard remains
intact; no deletion reconciliation was performed as part of the option correction.

## Explicit restart of unstarted MDs under a pinned installation (2026-11-06)

**Observed continuation and authorized intent:** after requesting an explicit
2026-3 MD path, the user still encountered iter3's missing-parent failure because
iter2's 24 terminal startup failures retained the old frozen environment and had
consumed their automatic retries. Recover only that failed upstream MD work;
preserve iteration 0/1, all prior scores, compound identities and parent rules.

**Implementation:** add a caller-locked, explicit `restart_unstarted_md_selection`
DB operation. Require the exact saved selection, latest iteration with no later
frozen CReM generation, all selected compounds failed/unscored with no result,
and one verified FAILED/JOB_SUPERVISOR_NOT_STARTED native parent per replica,
without a timeStarted or children. Block pending retries, active/uncertain jobs,
started MDs and changed science. Only the execution suite and fresh remote/local
run/helper paths may change; source/hash, protocol, seeds, native helper hashes,
resource limits, local Prime installation and selected order remain fixed.

Back up committed SQLite/WAL, preserve the old selection and native evidence in
md_startup_restart_history, archive local progress, and retain old remote journals
and jobs. Claim the same IDs as MD-running using the fresh frozen settings.
Keep consumed automatic retry tokens unchanged: this is one explicit new attempt,
not a replenished automatic budget. Reopen completion timing and mark FEP for
refresh after the real MD stage completes. Normal polling never retargets a saved
run or invokes this operation. Do not bypass the finite-MD parent gate.

**Verification:** 324 configuration, MD, retry, admission, local Prime and pool
regressions passed. Tests cover backup/selection/ranking preservation, retained
retry tokens and refusal of live/started/unproven work, changed protocols, scored
compounds and pending retries. An isolated production-DB copy passed the recovery
checks before the actual operation. Compileall, imports and diff checks passed.
These checks do not validate native MD completion or binding accuracy.

**Operational outcome:** verified all 24 old native parents against their remote
journals, checked the 2026-3 environment, held driver/all-stage locks, archived
SQLite and local MD state under output/.previous/iter02-md-startup-restart-1793943423275847029/,
and retargeted exactly those 24 IDs to a fresh 2026-3 run. Other compound rows
were unchanged. A single iteration-2 driver was started, then stopped gracefully
when fresh native queries showed the same startup failure. Fourteen new parents
were FAILED/JOB_SUPERVISOR_NOT_STARTED with actual jobSchrodinger=2026-3; ten
were not submitted. No native jobs were canceled or scientific failures hidden.
The initial md-running reports were submission-time snapshots, not proof of MD
startup or completion. Official `jsc admin test-queue slurm-gpu` without --submit
also failed to resolve runtime installation for the server's default launch
path. Source/TOML pinning is in place, but supervisor/queue environment diagnosis
remains unresolved; no further production retry was performed. Preserve all
journals and resume only after addressing the native startup problem.

## Pin new MD execution to Schrödinger 2026-3 (2026-11-06)

**Confirmed user request:** use an explicit 2026-3 execution installation in the
source/TOML instead of the mutable `/share/opt/schrodinger/default` path.

**Implementation:** change the optional `md-gbsa-schrodinger` default in config.py
to `/share/opt/schrodinger/2026-3`, and set the same explicit key in the EGFR CReM
pipeline TOML. This supersedes the earlier default-symlink choice for new runs.
Preserve explicit alternative TOML values as remote paths; do not resolve them on
the client. Leave local Prime installation settings and scientific parameters
unchanged. Existing SQLite-frozen MD selections, helpers, JobIds and retry budgets
retain their saved environment. Configuration changes alone do not retarget or
restart an already failed/uncertain MD run; that requires explicit reconciliation.

**Evidence limits:** the observed iter2 failures were all
JOB_SUPERVISOR_NOT_STARTED. JobServer was 2026-3 and the default path selected
2026-1; this difference alone does not prove the cause. A newer JobServer can
support an older Suite. Pinning removes ambiguity from future execution paths,
without establishing that native MD startup or calculations will succeed.

**Verification:** 225 configuration/MD regressions passed, including explicit
remote overrides and retention of frozen old settings. Compileall, package/CLI
imports and diff checks passed. Read-only checks found executable 2026-3 run,
multisim and supervisor tools on slurm/gpu56, with API v1.21.2; no job was submitted
or retried. Production SQLite and existing remote scientific helpers were untouched.

## Explicit recovery after deletion of all generated iterations (2026-11-06)

**Observed report and user confirmation:** after the prior iter04-only repair,
the user deleted all generated compound rows and iter01–iter04 directories.
Reference iteration 0 retained 24 fully scored compounds, but frozen populations,
selection records and 72 completed generated-compound MD results remained orphaned.
A local 100-run loop repeatedly retried generation. Its loop/supervisor were
stopped gracefully without canceling native jobs. Remaining iter04 Glide parents
and children became terminal during inspection; local and registered remote
native-job queries then reported no active work.

**Explicit recovery implementation:** `reset_deleted_crem_iterations` handles only
user-directed recovery of an entirely deleted generated population, under caller-
held driver/all-stage locks and verified terminal native/remote work. Require
retained references, no generated compounds, an exact explicitly named set of
completed positive CReM plans, full pose membership and consistent druglike
ownership. Any selected MD identity must have a finite saved matching result;
missing/uncertain ensembles block recovery. Unnamed later metadata, references to
deleted IDs and unidentified orphan records block or roll back the transaction.
Back up SQLite including committed WAL, then remove only generated-iteration
metadata and owned orphan records. Preserve every reference record and the ID
sequence. The automatic deletion guards and single-iteration reset remain intact.

**Operational verification:** first verified on an isolated copy, then backed up
and reconciled the project under all locks. The backup retains populations of
1090/1783/83/3150 and all 72 generated MD results. Reference compound rows,
iteration-0 metadata, its 24 MD results and scientific files were unchanged.
Archived native status, configuration, log and stale distribution reports under
output/.previous/iterations01-04-user-deleted-1793933228058394462/.
Foreign-key/integrity checks passed and read-only driver selection returned 1.
No new calculation was started and no TOML setting was changed by the repair.

**Verification:** 182 CReM, MD and workspace regressions passed, including backup
preservation of completed MD, multiple deleted iterations, unresolved MD refusal,
ownership mismatch and rollback for unknown orphans. Compileall, package/CLI
imports and diff checks passed; the full suite was not rerun. These tests establish
recovery/state behavior, not scientific accuracy.

## Explicit reconciliation of a user-deleted CReM iteration (2026-11-06)

**Observed report and user confirmation:** the user deleted the iter04 folder and
compound rows. SQLite retained a completed 36-member CReM population and orphaned
pose/druglike records. The normal frozen-population guard correctly stopped.
Remote Amber array 188500 still owned the deleted identities; no reset was
permitted while it was active. Read-only status and task-lock checks subsequently
confirmed all 18 tasks terminal, with 18 saved results.

**Implementation repair:** retain automatic CReM deletion guards. Extend the
existing DB reset helper with explicit `allow_deleted_crem=True`, solely for
user-directed reconciliation after stage/driver locks, native job checks and
archives. Require zero remaining compounds, a completed positive frozen count,
exact retained provenance membership, consistent selection ownership, no later
saved generation/ranking work and no durable MD selection. Back up committed
SQLite/WAL before removing orphaned CReM pose/plan and existing reset-scope
selection/scaffold/workspace records. Preserve previous compounds and native
results in backups; never cancel jobs, silently reset frozen work, or recover
from ID-range guesses. Normal execution never enables the explicit option.
Include iteration and expected/actual counts in mismatch diagnostics.

**Verification and operational result:** 174 CReM/MD/workspace regressions passed,
including explicit-reset backup, prior-result preservation and rejection of
nonempty, incomplete-provenance, MD-owned and later-plan cases. Compileall,
package/CLI imports and diff checks passed. After array 188500 was terminal,
reconciliation held the driver and all stage locks, archived the full remote
Amber run, 18 results and SQLite backups under output/.previous/, and removed
only orphaned iter04 metadata. All 2,980 remaining compound rows were unchanged;
foreign-key/integrity checks passed and read-only driver selection returned 4.
No calculation was started or canceled, and the project TOML was not changed.

## Optional fixed-receptor repulsion during 3D optimization (2026-11-06)

**Confirmed user request:** account for the receptor during energy optimization
so that generated poses can avoid steric collisions.

**Implementation choice:** add opt-in `[combichem-3d].receptor-repulsion-strength`
(default 0.0; finite nonnegative). Positive values minimize ligand MMFF/UFF plus
`k/2 * sum(max(0, R_lig + R_rec - distance)^2)` with SciPy L-BFGS-B and analytic
gradients. The receptor is fixed and uses the final bump checker's atom policy;
all vdW overlap contributes, while the independent final overlap tolerance and
all existing geometry/stereo/internal-clash gates remain unchanged. Neighbours
are recalculated every evaluation. Hard-fixed core atoms are excluded from
optimization variables; restrained cores retain their force-field constraints.
Apply to methyl probes and products. Preserve ligand-only energy reporting and
record the total objective, penalty, strength and convergence separately. Keep
iteration-limit evidence and unresolved numerical failures. Freeze the strength,
implementation and SciPy version under existing restart checks. Never reset,
extend or relabel completed/frozen populations to apply the new protocol.

**Evidence limits:** this is a steric rescue term, without receptor relaxation,
electrostatics, attraction, solvent or directional H-bond/metal terms. It is not
an affinity calculation. A trial strength of 100.0 and 250 iterations are
implementation experiment values, not calibrated or user-required defaults.
Real RDKit synthetic tests check gradients, clash rescue, fixed coordinates,
MMFF/UFF support and final rejection of immovable clashes. A read-only sample
of 12 evenly spaced iter3 rejected attempts, retaining each seed and candidate,
passed 0/12 at original ligand-only 50 iterations, 3/12 at ligand-only 250 and
12/12 at receptor-aware 250 (strength 100). This small selected sample does not
establish whole-population rescue, binding accuracy or production throughput.
No external programs, production DB writes or job submissions were used.

**Verification:** 397 related configuration, 3D, CReM and pipeline regressions
passed; the previously documented real-fragment collision assertion was explicitly
excluded. Editable installation, compileall, package/CLI imports and diff checks
passed. The full suite was not rerun. The EGFR TOML was not changed by this work.

## Reject standalone growth for scored CReM pipeline TOMLs (2026-11-05)

**Observed user report:** after reference scoring, logs showed repeated CReM
cycles without docking/GBSA. Read-only inspection found only 24 scored iteration-0
references in the main project DB; generated molecules were in a separate
standalone `output/combichem-3d` DB with no scoring results. The traceback and log
paths identified the standalone `graft combichem-3d` entrypoint, not the pipeline
Generate worker under `iterNN/02-generate/crem/`.

**Implementation repair:** refuse a non-export standalone invocation when the
TOML has top-level `generation-method="crem"`. Explain that standalone growth
has no scoring stages and print the corresponding `graft run` command. Check
before configuration/preflight, SSH dispatch, locking, output reset or calculation.
Keep `--export-only` available to recover saved standalone results and leave
legacy standalone TOMLs and the normal integrated pipeline unchanged. Preserve
both DBs, reference scores, jobs and all existing growth outputs. Do not relabel
standalone internal cycles as scored GRAFT iterations or import them implicitly.
Resume via the normal driver, which retains reference recovery/readiness checks.

**Verification:** 53 integrated/standalone CReM regressions passed, including
rejection through both CLI entrypoints at normal/debug/trace verbosity before
configuration or mutation, export-only preservation, and the existing integrated
scoring/generation tests. Compileall, package/CLI imports and diff checks pass.
Live project inspection was read-only; no production results were reset, imported,
recomputed or submitted during this repair.

## CReM generation in the scored GRAFT pipeline (2026-11-05)

**Confirmed user request and interview decisions:** replace REINVENT generation
with CReM as an opt-in pipeline backend. Keep iteration 0 exactly as before.
Start iteration 1 from the extracted reference-mcs frame, use grow-only fragment
addition, retain methyl-probe and fixed-frame 3D clash gates, and remove separate
CReM cycle progression. Each GRAFT iteration performs one expansion before the
existing Drug likeness -> LigPrep -> constrained Glide -> Amber -> Prime ->
MD-GBSA stages. Keep position-wise candidate caps and fragment sizes. Measure
the generation target after validity/novelty/3D admission but before drug
likeness; if the available candidate pools exhaust below target, continue with
the actual population. Use `elite-count` for subsequent parent selection and
retain the configured downstream filtering/calculation policies.

**Implementation choices:** top-level `generation-method="crem"`; the existing
`[combichem-3d]` table supplies the library/probe/search settings, but must omit
internal iteration, elite, output and stopping settings. Keep the initial frame
and docking core fixed. Select up to top-level `elite-count` finite, completed
MD-GBSA-ranked parents from exactly N-1; no QED/Prime/Glide fallback or TL. If
fewer finite parents are available, use those; zero finite parents stop explicitly.
Growth uses the saved fixed-frame CReM pose/UID/branch history of the selected
identity, not a guessed atom mapping from a subsequently protonated/docked/MD
structure. Existing scored stages still retain the lowest Glide score and its
corresponding pose under their original CMPID and recovery policies.

The Generate worker owns a durable local task DB inside its iteration's
`02-generate/crem/` folder. It seeds only N-1 parents and runs exactly one expansion
numbered N; this is calculation task coordination, not an additional scientific
cycle or a second ranked-parent loop. The driver continues supervising one GRAFT
iteration with independent sequential stage subprocesses. The main project DB
freezes settings/parents and atomically imports novel generated identities,
fixed-frame pose/provenance and an explicit actual-population completion marker.
Only that marker admits a below-target population to existing downstream gates.
Incomplete generation, failed workers, pending journals and time limits do not
establish completion. Known project identities are excluded from the generator's
target count. A zero-candidate exhausted iteration terminates the search without
launching empty scientific stages. Frozen populations may not be deleted/reset
or extended on resume. Missing selected-parent provenance stops, without refill.

This backend does not call the standalone CReM CLI or its output-reset function.
Use a new project output; do not convert/reset existing standalone runs, production
DBs or submitted jobs. Keep standalone CReM and legacy REINVENT/combichem behavior.
The separate EGFR pipeline example retains Glide top 500 for Amber, Amber top
100 for Prime, Prime top 24 for 5-ns MD with 20 snapshots, and explicit existing
optional FEP export settings. Actual selection/routing and reference policies
remain in the existing stage implementations; no external commands are invented.

**Verification:** editable installation with the CReM extra succeeded. The final
full-suite regression run passed 1,973 tests with seven skips and the previously
documented real-fragment collision assertion explicitly deselected. An earlier
unfiltered run reproduced that known assertion; new configuration compatibility
failures found during development were fixed and retested. Eighteen dedicated
pipeline tests cover one-step growth, raw-target capping, exhaustion, frozen MD
parent order, coordinates/provenance, drug-likeness/score gates, import rollback,
worker failures, cached-work recovery, changed-settings rejection, zero-candidate
termination and unchanged reference stages. Compileall, imports, CLI help and diff
checks pass. Production TOMLs, databases and submitted jobs were not modified.

**Evidence limits:** synthetic RDKit/CReM integration and mocked scoring tests
validate graph preservation, stage gates, ranking, target exhaustion, restart and
transaction behavior. They do not validate EGFR affinity, native docking/GBSA/MD
calculations or production throughput. Verification uses isolated temporary projects.

## Batched CReM probe and result persistence (2026-11-05)

**Confirmed user request:** keep worker results in memory temporarily and write
results/probe records to SQLite in groups, retaining short DB claim/completion
operations and restart recovery.

**Implementation choice:** CReM defaults are `batch-size=64`,
`result-buffer-size=64` and `result-buffer-seconds=5.0`; explicit TOML values
remain authoritative and legacy fragment defaults are unchanged. Probe updates
use the same bounded count/time policy, flushing on parent-plan completion or
exit. The interval is checked between completed updates, never by interrupting
a scientific calculation. Journal every completed probe decision before CReM
generation and every completed result before buffering; keep per-record fsync.
Bulk probe commits retain decision history and idempotently replay after a
crash between commit and journal deletion. A separate recovery worker imports
both probe and result journals under the stage lock before claims resume.
Only a fully persisted parent plan becomes ready for allocation. Keep SQLite
WAL/busy timeout/foreign keys, atomic claims, cycle barriers, scientific gates,
selection and existing saved results intact. Do not reset or restart live runs.
Progress reflects committed work and may lag buffered calculations. These
changes reduce transaction frequency; production throughput is not benchmarked.

**Verification:** 544 related combichem/configuration regressions passed, with
the previously documented real-fragment collision assertion explicitly
excluded. Added coverage checks grouped probe commits, decision-history replay,
worker termination, failed commits/transaction rollback, partial/corrupt journals,
recovery before claims, count/time/exit flushing, CReM pool reuse and explicit
configuration overrides. Compileall, package/CLI imports and diff checks passed.
These tests do not validate production receptor accuracy or performance. Existing
scientific projects and running jobs were not changed or restarted.

## Per-cycle CReM SMILES and SDF exports (2026-11-05)

**Confirmed user request:** save passing compounds separately for each cycle,
in both SMI and SDF format.

**Implementation choice:** publish `cycle01.smi`, `cycle01.sdf`, etc. in the
configured output directory after each cycle's workers finish and during final,
partial and export-only exports. SMI contains one `SMILES<TAB>cycle_name` row per
passing generated identity within that cycle. SDF preserves all passing generated
poses, their coordinates, cycle names, CMPID, lineage and atom-cycle metadata.
Exclude input frames and output-filter failures; keep repeated identities in
separate cycles. Empty exported cycles have empty files. Replace each file
atomically after staging; retain existing aggregate exports. Exporting never
resets calculations or modifies saved scientific results. Regression tests check
export grouping and metadata, not scientific accuracy.

**Verification:** 243 related regressions passed, with the previously documented
real-fragment collision assertion deselected. Compileall, package/CLI imports
and diff checks passed. Export tests cover cycle grouping, repeated identities,
multiple poses, 3D and atom metadata, filtered/input exclusion, empty replacement,
repeatable exports and unchanged saved compound/pose records. Existing active
supervisors retain their loaded code; do not restart/reset them to load this change.


## Cycle names and final SMILES export for CReM (2026-11-05)

**Confirmed user request:** display passing compounds by cycle as `01_0001`,
`01_0002`, `02_0001`, etc., and save their SMILES to a file at the end.

**Implementation choice:** assign export/display labels to distinct generated,
output-passing identities within each cycle, in ascending SQLite compound ID
order. Start numbering at one per cycle; multiple poses share a cycle name.
A repeated identity in another cycle gets a name for that cycle. Keep internal
CMPID names/IDs, parent links, claims, provenance and scientific results intact.
CReM RESULT/parent previews and iteration inventories expose the display name.
At final/partial/export-only export, atomically publish `compounds.smi` as
`SMILES<TAB>cycle_name`, one row per passing cycle/identity. CSV remains globally
deduplicated: `name` is the first passing cycle label and `cmpid` retains CMPID.
Passing SDF titles use cycle names with `cmpid`/`display_name` properties; preserve
all poses. Histories include the display name. Legacy fragment exports retain
their CMPID naming. Export-only must not delete or recalculate existing results.


**Verification:** 237 related regressions passed, with the previously documented
real-fragment collision assertion explicitly deselected. New coverage checks
multiple poses sharing a name, repeated identities across cycles, CSV/SDF/SMILES
mapping, excluded inputs/filter failures, empty output, repeatable exports and
unchanged scientific DB identities/history. Compileall, imports and diff checks
passed. Export-only updated the existing EGFR result artifacts without resetting
or recalculating compounds: 46 SMILES entries, distributed 13/22/10/1 across
cycles 1–4, now carry the requested cycle labels.


## CReM CLI starts fresh in existing output (2026-11-05)

**Confirmed user requirement:** if the existing CReM output folder exists, delete
its contents and run anew instead of failing on changed initial-frame settings.
This supersedes ordinary CReM CLI resume behavior in the earlier CReM section;
internal claim/journal recovery and legacy fragment-backend CLI behavior remain.

**Implementation:** after whole-run SSH dispatch, acquire the existing growth
stage lock before any deletion. Remove only the configured dedicated CReM output
contents, retaining the held lock inode to exclude concurrent old/new supervisors
and orphaned local fork workers. Clear DB/WAL, frame/native caches, journals,
logs and exports; no archive is created. `--export-only` never clears output.
Reject output containing configured input paths or unknown/unreadable saved DBs,
and retain saved remote-pool/Slurm evidence instead of resetting uncertain work.
Do not change receptor preparation, geometry, CReM chemistry, rankings, CPU
allocation or unrelated project output. Regression tests use isolated temporary
projects; no existing scientific project is deleted for verification.


**Verification:** 235 related regressions passed; the previously documented
real-fragment collision assertion was explicitly deselected. Coverage includes
changed-budget CLI relaunch, export-only retention, live-lock exclusion, stable
lock inode, input containment, symlink-target retention and saved remote-job
protection. Compileall, imports and diff checks passed. Cleanup tests only removed
isolated synthetic outputs; existing EGFR results were not removed during testing.


## Optional fixed-frame CReM growth (2026-11-05)

**Confirmed user requirements:** reimplement 3D growth using CReM with the supplied
`/home1/crem/database/chembl22_sa2_hac12.db`, existing project TOML inputs, local
multiprocessing and direct SSH execution. The input `reference-mcs` is the fixed
starting frame. `[*:N]` caps the number of fragment additions originating at that
attachment, while bare `[*]` is unlimited. Preserve the rest of the frame against
substitution via CReM `protected_ids`. Before generation, methyl-probe all
H-bearing sites across the entire active branch and block sites without a passing
pose. A child adds exactly one fragment and consumes one count on only that root.
Keep the original frame coordinates fixed, relax sidechains, and check both
receptor and internal clashes. No elite-MCS update or new docking stage.
User accepted up to ten candidates per site, 5–10-heavy-atom fragments and up to
100 QED-ranked distinct previous-cycle parents as the starting policy.

**Implementation choices:** opt-in `[combichem-3d].generation-backend="crem"`,
`crem-db`, context radius 3 and minimum frequency 0. This selects `fixed-frame`;
legacy fragment/frontier/frame-cycle behavior remains separate. Extract the core
from the first compatible reference pose, removing its external decorations.
All passing single-branch intermediates are output candidates and eligible
parents. `[*:999]` is literal 999 for CReM; absent numbers use null/unlimited.
Every H-bearing heavy atom is one logical site with independent H-direction
starts; any passing direction admits it. CReM uses a selected `replace_ids` site,
all other heavy atoms protected and symmetry fixes enabled. Reconstruct each
CReM candidate with persistent UIDs and require identical isomeric SMILES before
joint constrained 3D search. Never retain methyl probes or bypass context matching.
Default finite search uses two starts, two torsion trials and 100 minimizer
iterations; iteration-limited poses still need all existing geometry/stereo gates.
CReM calls use one core, while SQLite-claim workers provide multiprocessing.
Freeze ranked parents transactionally only after all prior-cycle work finishes;
preserve saved selections, pools, cursors, journals and results on restart.
Fingerprint the immutable library main file and nonempty WAL, CReM version,
settings and implementation. Stream file hashes to bound RAM. Use fresh output
for the new protocol; never convert existing runs or reset their calculations.
Support whole-supervisor `execution-host` SSH forwarding on shared paths; reject
Slurm/distributed worker-host combinations for this backend. Retain legacy native
input extraction and independent existing Glide/GBSA workflows.

**Evidence limits:** regression and small synthetic RDKit/CReM runs verify graph,
provenance, state, coordinate and workflow behavior. They do not establish real
receptor accuracy, affinity or production-scale throughput. QED is not affinity;
the fixed-receptor radius-overlap filter is separate from ligand-only optimization.
See `docs/COMBICHEM_3D.md` and the CReM settings example for inputs and limitations.


**Verification:** 17 dedicated CReM tests pass, including real CReM-generated
synthetic DB chemistry, explicit protection, capped/unlimited branches, probe
rejections/unresolved evidence, atomic concurrent parent claims, fixed coordinates,
restart and library-change guards. The full suite (before two final test-only
additions) passed 1,935 tests with seven skips and the previously documented
real-fragment collision assertion failure in `tests/test_combichem_3d.py`.
Editable installation with the CReM extra, compileall, imports and diff checks pass.
A separate synthetic project using the supplied ChEMBL DB and requested frame
ran two CPU workers, resumed from two to six configured cycles and ended naturally
with 44 attempts / 43 unique passing molecules. Its distant single-Ne receptor
provides no EGFR validation or production throughput evidence. Production TOMLs,
databases, native jobs and scientific results were not edited or submitted.


## Amber progress messages only on change (2026-11-04)

**Confirmed user request:** suppress repeated Amber polling messages while the
compound count remains unchanged.

During supervision at normal log verbosity, show the initial Amber status and
print another pending line only when the remaining count or waiting reason
changes. Elapsed time alone does not trigger output. Keep the latest persisted
elapsed time on displayed updates. Worker recovery banners are verbose-only for
saved supervised runs; the redundant remote-work-pending sentence is omitted
under supervision, while standalone worker diagnostics remain available. Keep
work-start signals, every scientific/status/collection poll, result persistence,
completion lines and nonzero worker failures unchanged. Pending comparison state
is scoped to one supervised stage/iteration; a restarted driver reports its
initial status again. No database/job/resource settings are changed.


## Slurm JobServer upgrade and optional MD installation override (2026-11-04)

**Confirmed user report/request:** the Slurm JobServer is upgraded to 2026-3;
remove the EGFR `md-gbsa-schrodinger` entry when unnecessary.

The override remains optional, and the EGFR TOML omits the key. A subsequent
**confirmed user request** changes the MD-GBSA remote installation default to
`/share/opt/schrodinger/default`. Keep that path remote; do not resolve it on the
local launch host. Explicit overrides retain their configured values.
Do not replace pinned old MD scientific commands/helpers merely because a server
was upgraded; old selections retain their recorded installation and protocol.

Runtime checks verified server and current client API v1.21.2. The Slurm user's
previous TLS certificate was stale after upgrade; reauthentication restored job
queries with both the current and older clients. Do not treat inaccessible-server
errors as an empty queue or remove protected remote registrations to bypass them.
LigPrep preparation now reports native/remote discovery failures as pending,
retaining files and claims rather than printing repeated tracebacks. It never
submits while prior-work status is uncertain. The already running supervisor
subsequently completed LigPrep and submitted native Glide groups to `slurm-cpu`.
This is routing/recovery evidence, not scientific accuracy validation.


## Automatic retirement of old iteration workspaces (2026-11-04)

**Confirmed user request:** stop repeated Generate tracebacks from stale remote
sampling markers and start with a clean calculation folder when prior data exists.

**Implementation choice:** retain scientific data by archiving the entire old
iteration under `output/.previous/`, then use the existing transactional deleted-
population reset and SQLite/WAL backup. Do not delete iteration 0, saved nonempty
populations, resumable zero-row plans, live jobs or uncertain submissions.

A REINVENT marker alone no longer blocks cleanup. Verify its exact saved remote
configuration and inspect remote same-user processes, including children retaining
the remote working directory. New SSH invocations save an atomic host/run/config
exit receipt; SSH transport exit 255 is uncertain. A receipt alone never retires
live work. For legacy runs without a receipt, require the native final log line,
matching config, nonempty output, matching local output when present and absence
of related remote processes. The installed REINVENT terminal line follows its
runner/controlled shutdown; this verifies retirement, not scientific validity.
Only known non-dumpable credential/session daemons are excluded from cwd checks;
an inaccessible unknown/scientific process remains uncertain.

For supported saved Slurm Amber arrays/single jobs, require complete native
terminal evidence and released remote worker locks before archiving. Preserve the
remote trees and write reconciliation evidence inside the local archive. Unknown
remote types, incomplete evidence and transport failures retain all work. Expected
workspace reconciliation/lock waits print a concise pending reason and return
without sampling, rather than producing a traceback on every supervisor poll.
All native LigPrep/Glide/Prime retirement checks and ownership/reset constraints
remain in force. Cached output or a zero local worker exit alone is insufficient.

New Slurm LigPrep/Glide submissions also perform a read-only native host/server
API check before launching; LigPrep checks before claiming new compounds or
writing a launch record. An older JobServer API produces a pending message,
never an automatic client downgrade, server restart or duplicate submission.
Existing LigPrep launches lacking a recoverable JobId remain pending without
repeated tracebacks. The observed Slurm server API 1.19.4 is older than the
2026-3 launch client's API 1.21.2 and must be upgraded separately before new
Slurm scientific submissions can succeed. A verified API-rejected LigPrep intent
was archived only after successful native history queries found no matching
command; all older jobs at the reused directory were terminal. No live job or
compound score was reset.

**Verification:** 234 affected tests passed. The full suite passed 1,841 tests
with seven skips and the same previously observed 3D combichem collision-test
failure. Editable installation, compileall, imports and diff checks passed.
The current deleted iteration was automatically archived and regenerated to
10,000 compounds; all 24 reference rows matched the pre-reset SQLite backup.
Remote verification was read-only; scientific validity/throughput is not inferred
from these workflow and mocked-program tests.


## LigPrep and Glide through Slurm CPU hosts (2026-11-04)

**Confirmed user request:** submit LigPrep and Glide to `slurm-cpu`, alongside
Amber's existing Slurm execution.

**Implementation choice:** `use-slurm=true` now selects native host entry
`slurm-cpu` for new LigPrep and Glide jobs, including the legacy combined docking
entry point. False/omission keeps `host`. Optional `ligprep-host` and `glide-host`
override these per-stage destinations. `host` continues to control the ordinary
Prime fallback; local Prime and MD backend settings retain their separate roles.
Keep `ligprep-cpus` and `glide-cpus`: EGFR uses 124 for LigPrep and a shared total
124 budget across parallel Glide reference groups. Native JobDJ `-HOST`/`-NJOBS`
perform submission through the registered Slurm JobServer; do not wrap these jobs
in new unmanaged sbatch processes. Amber retains its existing Slurm arrays.

Preserve LigPrep protonation/stereochemistry, constrained SP docking, CMPID title
mapping, lowest-score pose selection, and immediate per-group persistence. Saved
LigPrep/Glide native JobIds, commands and hosts remain authoritative after flags
change. Live/uncertain old-host Glide reservations defer new-host submissions
until those reservations retire. Do not cancel, relocate or reset existing work.

The launch user's JobServer certificate collection must include the Slurm server
and expose `slurm-cpu` for the matching installed release. Read-only installed
2026-3 help verifies `-HOST`, `-NJOBS` and JobServer discovery; the current launch
account was authenticated with the already running Slurm JobServer, which lists
`slurm-cpu` and `/share/opt/schrodinger/2026-3`. No service startup is performed.
Regression tests mock scientific calculations; no LigPrep/Glide accuracy or
Slurm throughput validation is claimed by this routing change.

**Verification:** 292 affected regressions passed, with 231 configuration/command
and resource-allocation tests rerun after final compatibility refinement.
Editable installation, compileall, package/CLI imports and diff whitespace checks
passed. The full suite passed 1,814 tests with seven skips and one previously
observed failure in `test_combichem_3d.py`'s real-fragment collision assertion;
that implementation/test was not changed here. No new scientific jobs were
submitted for this routing verification.


## Explicit reference MD completion (2026-11-04)

**Confirmed user request:** begin with MD-GBSA for iteration 0's four references
omitted by the original Prime top-20 selection, retaining the twenty completed
scores. Separately, stop and preserve iteration 1's old calculations and start
that deleted population afresh after reference work is complete.

**Scoped maintenance implementation:** after native retirement, collection and
cleanup are verified, extend the completed reference selection explicitly with
unscored eligible reference IDs. Require unchanged source fingerprint, protocol
and replicate count, compare the old selection in the claiming transaction, and
use fresh remote/shared run directories. Preserve old selection/settings in
`md_gbsa_selection_history`; append IDs and claim only the added compounds.
Original scores and per-compound provenance remain unchanged. Ordinary polling
does not expand frozen selections or retry resolved work. Stage locks and SQLite
backups protect the operator action. Iteration 1 reset continues to require
verified terminal native jobs, released remote locks, a complete archive and the
existing non-destructive deleted-population reset; reference compounds are kept.


## Prime display names (2026-11-04)

**Confirmed user request:** replace the obsolete Adam name in runtime messages
with Prime. This changes log, pending/error and completion wording only. Keep
legacy mode/backend values, host defaults, artifact names and saved/frozen helper
files intact for recovery compatibility. Scientific commands and scores do not
change; historical logs and already submitted helpers are not rewritten.


## Configurable 192-core local Prime host (2026-11-04)

**Confirmed user request:** the `prime` host has 192 CPU cores; support that
capacity and derive `-NJOBS` from the actual input MAEGZ frame count rather than
assuming twenty frames per molecule.

**Implementation choice:** local ordinary Prime and MD-GBSA accept integer slot
budgets 1..192, retaining the default 16 and default `adam` host. This supersedes
the 1..16 validation limit below for new requests. Single-host parent exclusion,
cross-project submission guards and durable recovery remain unchanged. The EGFR
TOML initially selected 180 slots for operational headroom. A subsequent confirmed
user request to use all 192 cores raises both local slot settings to 192 for new
work; MD snapshot selection remains twenty. This is a concurrency ceiling, not
measured throughput or a license/memory guarantee. Folder count is not subjob
count. More subjobs than frames/compounds cannot create useful parallel work.
Report other active Prime jobs and remote pending reasons explicitly instead
of silently returning a generic submitted/running message. Preserve external
manually submitted jobs and frozen existing commands; do not stop or repartition
them without explicit authorization.
Ordinary Prime still has one subjob per original compound, whereas MD-GBSA pools
ready frames from multiple original compounds into one native queue.

Count the actual per-molecule MAEGZ entries before admitting new MD Prime work;
reject empty inputs and disagreement with the pinned scientific snapshot count.
Set pooled `-NJOBS` from these counts and verify it against every frame written
to the combined MAEGZ before publication/submission. Keep `-HOST localhost:<slots>`
independent of the number of frames. Recover saved native commands and frozen
helpers without rewriting host, resources, protocol or JobId. A saved `adam`
submission cannot be migrated merely because the current TOML selects `prime`;
an unreachable saved host remains pending and must be reconciled separately.
No production jobs, claims or scientific results are reset by this change.
Regression tests use mocked structures/native jobs, not scientific calculations
or a 192-core throughput benchmark.


## Project rename (2026-11-04)

**Confirmed user request:** rename the software to Graft throughout project
source, documentation, tests and example configuration. The distribution, Python
package and console command are `graft`; stages run as `python -m graft.stages.<module>`.
New helper names, job labels, cache paths and project-specific metadata use the
same name. Scientific algorithms, parameters, database claims and stage gates
are unchanged by this rename.

Existing production databases, output trees, logs, frozen workers, manifests and
native jobs are not rewritten or migrated. Compatibility with artifacts carrying
the previous namespace has not been established; do not resume an existing run
with the renamed package without reviewing its pinned helpers, metadata and shared
submission registry/locks. This rename does not authorize resetting or resubmitting
jobs. Existing installation and checkout directory names are not renamed.

## Amber charge-generation deadline (2026-11-02)

**Confirmed user request:** limit ligand charge generation to ten minutes.

New Slurm/direct-SSH manifests pin `charge_timeout_s=600` independently of the
600-second complex-minimization deadline and the Slurm allocation. Propagate it
unchanged to every array task and calculator. Run antechamber in a dedicated
process session; at its deadline SIGKILL its process group (including SQM), wait
for the parent and record a terminal per-compound charge-generation TIMEOUT.
Do not proceed to parameterization/minimization/MMPBSA or use partial charges.
Native nonzero exits remain scientific failures. Existing scientific options,
pose restoration, claims, score persistence and downstream gates are unchanged.

Saved records without this field and already uploaded/submitted workers retain
their original behavior; no live process is canceled or implicitly resubmitted.
The limit covers the complete antechamber/SQM invocation, excluding earlier
receptor preparation and later parmchk2, minimization and GBSA. Tests use mocked
Amber programs plus real short Python parent/child processes to verify timeout
cleanup and inherited-lock release; no Amber accuracy validation is claimed.


## Amber checkpoint protocol validation repair (2026-11-02)

**Observed runtime bug:** new checkpoint input metadata was rejected by the
worker's legacy-only protocol guard before receptor/scientific preparation.
Accept the exact checkpoint protocol/settings with a pinned positive finite
minimization deadline, while retaining legacy ordinary/retry compatibility and
rejecting unknown protocols, changed settings and invalid deadlines. Producer and
worker share the protocol constants. Regression tests exercise the real worker
execute path instead of replacing it with a mock; 199 Amber regressions pass.

**Scoped production recovery:** iteration 63's original Slurm array 167242 had
500 terminal COMPLETED tasks, each with only the exact protocol-rejection log,
released locks, no receptor LEaP input, no ligand calculation log and no results.
Archive original manifest, native/task terminal evidence and a SQLite backup;
retain its remote tree and original workers. Replace only its local pending
manifest with a new owned array for the same 500 still-claimed unscored compounds,
using the corrected worker and unchanged scientific inputs/settings. Do not edit
submitted workers, reset database claims, or extend this repair to uncertain,
live or scientifically executed tasks. This is a scoped repair, not an automatic
retry policy or scientific accuracy validation.
Replacement JobId 167742 was submitted by the existing supervisor. Read-only
inspection confirmed identical scientific input apart from the unique job name
and 377 tasks had reached ligand calculation, with no protocol rejection.


## Default Amber checkpoint scoring (2026-11-02)

**Confirmed user request:** apply iteration 60's time-limited minimization and
saved-coordinate scoring to ordinary Amber calculations.

**Implementation choice:** newly created Slurm arrays and direct-SSH runs pin
`minimization_timeout_s=600`. Reuse the verified retry worker: save ASCII native
restarts every ten minimization steps (`ntwr=-10, ntxo=1`); after the minimization
deadline kill and wait for serial sander, validate the highest readable full-atom
finite checkpoint and run single-frame MMPBSA on those exact saved coordinates.
Normal native completion still requires valid final energy and coordinates.
Missing/invalid checkpoints and native scientific errors produce failures, not
scores. Existing ff14SB/GAFF2, restraints, 500 cycles, solvent and entropy settings
are retained. New input metadata distinguishes the checkpoint-capable protocol;
result JSON and archived `minimization_status.json` distinguish completed from
truncated minimization and retain the checkpoint step.

New Slurm allocations default to one hour, separately from the 600-second
minimization deadline. `amber-slurm-time-limit` remains a positive integer number
of allocation seconds; values below 3600 (including older TOMLs' 600) are raised
to 3600 for newly created records, and larger limits are honored. The one-hour
hard limit can still terminate unusually long preparation/scoring. Direct SSH
uses the minimization deadline without a Slurm allocation.

This supersedes the ordinary-new-work exception in the earlier explicit timeout
retry section and the ten-minute allocation default for new records. All saved
records, including unprepared records without the checkpoint policy, retain their
pinned settings, workers, inputs and native JobIds. No submitted jobs, old scores,
failed compounds or frozen downstream selections are reset or retried by this
change. Production iter61 failures require a separate explicit retry request.
Truncated structures are not claimed to be converged or equivalent in ranking
quality to fully minimized structures. Regression tests mock scientific programs;
this change does not add a new real-target scientific validation.

**Verification:** 178 dedicated Amber regressions and the full 1,721-test suite
passed. Editable installation, compileall, package/CLI imports and diff whitespace
checks passed. No production jobs or databases were changed.


## Optional Adam backend for ordinary Prime GBSA (2026-11-02)

**Confirmed user request:** add `prime-gbsa-local-compute=true` with the same
shared-folder/SSH/localhost calculation and result-collection behavior as local
MD-GBSA. Slurm is not required on Adam. Omission/false retains ordinary native
Prime scheduling and its seven-slot limit. Saved submissions retain the original
backend even when flags change or Prime is disabled.

**Implementation choices:** strict optional `prime-gbsa-local-host="adam"`,
`prime-gbsa-local-work-dir="/home1/prime_share"`,
`prime-gbsa-local-schrodinger="/home/schrodinger/2025-3-IL"`, and
`prime-gbsa-local-cpus=16` (integer 1..16). These are independent of MD settings.
Use the existing readiness/atomic candidate claims, iteration-0 reference handling,
final top-N ranking and lowest-Glide pose input preparation. Preserve ordinary
Prime `prime-gbsa-flexdist` (default zero), default ligand minimization and positive
distance sidechain-only flexibility (`-rflexgroup side`), including independent
complex inputs and COMPLEX output. Do not substitute the MD residue protocol.

Freeze mode `adam_prime_batch_v1`, ownership, IDs, input/helper hashes, exact
native command, Adam paths, slot budget and license retry policy before launch.
All compounds share a single Prime/JobDJ batch, one subjob per original compound,
`-HOST localhost:<slots> -NJOBS <compound count>`. Keep the global Adam sixteen-slot
reservation and Glide/Prime exclusion shared with MD and other projects. Legacy
JobControl IDs and modern UUIDs use the verified API adapter; parent/child status,
command/directory identity, lost submission responses, immutable completed child
outputs and finite compound identities remain mandatory. Missing shared folders
or unjournaled artifacts never trigger duplicate submission. No implicit service
startup or migration of saved native/individual queues is added.

Import completed immutable subjob scores/poses while the parent runs, commit
before recording imported IDs, and retain them if the parent later fails. Collect
final native complexes/logs/journals locally, validate native output checksum,
import final scores and publish prime_gbsa_final.maegz before cleanup. Confirmed
per-compound atomtyping failures use existing failure records; unexplained missing
results and uncertain/failed native work retain inputs and submission identity.
License-only terminal failures use pinned bounded retries and archive the prior
attempt after terminal verification. No generic failure is silently resubmitted.
After successful commit and coordinate publication, reverify native completion,
write a durable collection receipt and remove only the owned shared tree through
an atomic tombstone rename; resume interrupted cleanup without recomputation.
Mixed batches with confirmed scientific failures keep their shared inputs for
recovery. The saved local manifest blocks stage advancement until retirement.

See examples/prime-gbsa-local.example.toml. Production TOMLs/jobs/DBs are not
changed to enable this new option automatically. Regression tests mock external
calculations; no ordinary-Prime scientific accuracy or throughput validation is
claimed for this newly added backend.

**Verification:** the workspace full suite passed 1,697 tests. After final
streaming/diagnostic refinements, the 32 dedicated local-Prime regressions passed;
the broader affected suites passed 363 tests before the last dedicated regression.
Compileall, package/CLI imports and diff whitespace checks passed. Tests cover
strict config, pinned scientific commands, legacy/native recovery, shared-folder
loss, database-before-cleanup ordering, interrupted deletion, bounded license
recovery, streamed-score persistence after parent failure, immutable completed
child collection, and transient diagnostics with successful final scores. External
ordinary Prime calculations were mocked; production local-Prime work was not
submitted or enabled by development.

## Cross-molecule Adam Prime frame batching (2026-11-02)

**Confirmed user request:** remove per-molecule GBSA serialization that leaves
Adam cores idle at each molecule's batch tail, keeping the total CPU/license
budget at sixteen. Slurm installation is not required for this single-host mode.

**Implementation choice:** prepare ready molecule inputs before admission, then
freeze their frames into one native Prime/JobDJ queue with
`-HOST localhost:<budget> -NJOBS <total frames>`. Multiple molecules share that
queue, so free slots can process the next molecule's frames immediately. At
most one unresolved parent reserves the host budget across projects; active or
uncertain old single-molecule batches keep their exact command and finish first.
The operational adapter groups only unsubmitted requests and preserves frozen
helpers, individual MD inputs, explicit ligand ASL, 6 A/residue REAL_MIN settings,
compound/frame titles and per-molecule ensemble statistics. Replicas of the same
original compound use different batches to preserve unique native title keys.

Persist group intent and molecule bindings before launch; retain native JobId
and recover incomplete bindings/launches without duplicate submission. Checksummed
combined frames are immutable. Require verified native parent/child completion,
validate every compound/frame identity and finite score, and publish exact native
complexes back to their original molecule directories under an exclusive
publication lock. Native results are not replaced with recomputed coordinates.
Compound score publication waits for the pooled parent's completion; partial
ensembles never satisfy MD/FEP/TL gates. Original per-compound retries and cleanup
still apply; archive/remove pooled inputs and output only after every member's
complete ensemble has been committed and collected, retaining journals/receipts
locally. Failed/uncertain groups and unrelated directories are retained.

This supersedes per-molecule native parent serialization for newly submitted
Adam work, including unsubmitted work in an existing frozen run, as explicitly
requested by the user. Existing calculations/scores are preserved. Native batch
initialization, I/O and the final queue tail can still leave some cores idle;
this change does not promise continuous 100 percent CPU utilization or validated
scientific ranking accuracy.

**Verification:** 308 affected regression tests pass for multi-molecule batching,
identity/count/nonfinite rejection, old-job recovery, immutable input checks,
interrupted binding/launch recovery, replica separation and all-member cleanup.
Compileall, package/CLI imports and diff checks pass. In the existing iteration-59
run, the supervisor submitted one 14-molecule / 280-frame native parent and native
JobControl inspection confirmed sixteen simultaneous frame subjobs, with completed
frames refilling from the common queue. This is execution/concurrency evidence,
not validation of energy accuracy or an end-to-end throughput benchmark.

## Adam legacy JobControl recovery and explicit MD retries (2026-11-02)

**Observed runtime evidence:** Adam's installed Prime launches legacy IDs such as
`adam-0-6ae80757`, even with a local JobServer running. `jsc info` rejects those
IDs. Installed `schrodinger.job.jobcontrol.Job` records verified the parent and
20 child jobs as completed/finished; `isDownloaded()` documents legacy completed
output transfer, and `download()` is explicitly a no-op for legacy jobs.

**Implementation correction:** route Adam operations through a versioned,
checksummed `control/jobcontrol-<sha256>.py` adapter in the owned shared run.
Import the exact frozen scientific helpers and adapt status, launch recovery and
collection APIs only. Never replace their pinned bytes, commands, inputs or
protocols. Native UUIDs retain jsc handling; legacy IDs use the installed Job API,
recursive parent/child identity and completion, successful parent exit, exact
launch directory/command and downloaded status. Legacy active jobs supplement
native listings and retain slot/submission reservations. Unknown/missing records,
live children, mismatched identity and ambiguous lost replies block collection
or resubmission. No cached file/log/zero exit alone establishes completion.

**Confirmed additional user request:** recalculate only failed iteration-59
CMPID_618046, CMPID_619885 and CMPID_619897 after gpu81 time synchronization.
Queue explicitly authorized retries in SQLite, archive consumed retry tokens and
previous records in `md_manual_retry_history`, and retain the normal bounded
automatic policy. Workers still verify terminal native jobs before archiving and
rearming, preserving successes and scientific settings. Back up the production DB
and retain native terminal evidence before this scoped operator request. Do not
reset live/uncertain jobs, change scheduler/service settings, or grant indefinite
automatic retries.

**Verification:** 170 affected tests passed with mocked JobControl/native APIs
and real temporary SQLite/files. Compileall, package/CLI imports and diff checks
passed. Read-only native inspection verified the installed legacy Job API. The
running supervisor recovered CMPID_618812's existing 20-frame legacy Prime result
and committed its finite MD-GBSA score without resubmission. The three explicit
MD retries were queued only after native terminal verification and a SQLite
backup with committed WAL; the original failed attempts remain archived.

## Adam shared-folder MD snapshot Prime backend (2026-11-02)

**Confirmed user request:** opt-in `md-gbsa-local-compute=true` saves selected MD
frames under `/home1/prime_share`, shared with Adam, submits Prime through SSH to
Adam's localhost without Slurm, collects results, and deletes that run's shared
inputs/results only after successful collection. Adam has at most 16 CPU/license
slots. Omission/false preserves the existing Slurm Prime backend and seven-slot
limit. Frozen selections/jobs keep their exact original backend and protocol.

**Implementation choices:** keep Desmond and validated frame extraction on the
existing MD host; stream only checksummed frames, identity, frame metadata and MD
journal into a UUID-owned shared directory. Optional `md-gbsa-local-host="adam"`,
`md-gbsa-local-work-dir="/home1/prime_share"`,
`md-gbsa-local-schrodinger="/home/schrodinger/2025-3-IL"`, and
`md-gbsa-local-cpus=16` (strict integer 1..16) configure new work. Both machines
must see the share at the same absolute path; verify a pinned ownership record
through SSH before claims. Pin helper hashes and all backend/protocol settings.
The localhost JobServer must already be configured/running; never start services
or install Slurm implicitly.

Following the preceding 6-Angstrom flexibility discussion, this new mode uses
`-job_type REAL_MIN -rflexdist 6.0 -rflexgroup residue`, explicit ligand identity
and `-out_type COMPLEX`, without `-frozen`. This allows backbone and sidechain
movement within the selected residues and default ligand minimization. This is
an implementation choice for the new protocol, not an accuracy validation or a
change to legacy `ENERGY -frozen` scores. Record the protocol in every ensemble.
Adam's installed 2025-3-IL help confirms these options. Use one native parent with
`-HOST localhost:<slots> -NJOBS <snapshots>` and single-thread native environments;
serialize parents on Adam, retain uncertain-launch reservations, extend the
existing durable remote registry and Glide/Prime fencing to both JobServers.
Only this explicitly requested Adam backend raises the seven-slot ceiling to 16.

Workers alone transport/submit/collect; SQLite remains local and authoritative.
Reverify MD completion on its original JobServer, journal Adam intent/JobId before
or during submission, and recover exact native jobs after disconnects. Verify
terminal native work and complete finite frame identities before publishing.
Bounded in-cycle retries archive/rearm only Adam Prime when it fails, retaining
successful MD and replicas; live/uncertain jobs never reset or resubmit.

Copy final complex coordinates, frame metadata, inputs' hashes, result JSON and
native journals into `09-md_gbsa/adam-results/CMPID_<id>/replicaNN/` before score
commit. In the same scientific transaction insert a pending cleanup record in
`md_gbsa_local_cleanup`; keep MD/FEP/next-cycle completion closed until it is
acknowledged. After commit reverify Adam's native terminal success and collected
output digest/result, save a local cleanup receipt, atomically rename only the
owned shared CMPID tree and remove it. Resume interrupted deletion from the
receipt without recomputation. Remove run helpers/ownership only after remote
registry retirement and no remaining compound tree. Failed/uncertain/uncommitted
work and other projects' directories remain untouched. Original MD trajectories
and inputs on the MD host are retained.

**Evidence/limits:** read-only Adam SSH/help inspection succeeded; its local
JobServer was not running, so real Prime calculation/energy/throughput and
cross-version structure compatibility have not been validated. Regression tests
use mocked native calculation/status/transport and real temporary SQLite/files.
The full suite passed 1,635 tests; after final cleanup/lost-active-list hardening,
the affected suites passed 276 tests. Editable installation, compileall, package
and CLI imports, and diff whitespace checks passed.
See `examples/md-gbsa-local.example.toml`; production TOMLs, jobs and DBs were not
changed by development.

## MD-GBSA cycle completion and bounded retry (2026-11-02)

**Confirmed user request:** resolve selected MD failures within their cycle,
either by a further calculation or a final failure, before declaring completion.
**Implementation choice:** one automatic retry per selected compound for the
entire cycle, not one per supervisor restart. Register the retry durably in
SQLite as soon as the first complete ensemble attempt fails. Keep the MD/FEP
completion gate closed until recovery is consumed and every selected compound
has a finite complete ensemble or a second terminal failure. A consumed retry
is never reopened automatically on later driver invocations. Existing consumed
restart-retry records count as the single retry; preserve active/uncertain native
jobs and frozen selections, inputs, settings, successes and native journals.
Workers alone verify native termination and archive/rearm the failed phase;
completed MD/replicas remain reusable. Missing/ambiguous identity stays pending,
not final failure. No new remote helper or scientific protocol is required.

Adopt the policy for newly entered MD work and the latest saved MD cycle on
restart; do not reopen all historical cycles. For an adopted incomplete cycle,
audit prematurely saved MD/iteration finish timestamps in
`score_completion_history`, unset their authoritative finishes, and retain their
original starts. Finish again only after the bounded retries resolve; display
selected/scored/final_failed counts. This supersedes preserving an early finish
that incorrectly preceded MD retry resolution.

Defer FEP selection until MD resolves. For an adopted cycle whose FEP selection
was already frozen early, refresh its MD-ranked candidates once after recovery,
archiving the old selection in `iteration_fep_selection_history` and the old TSV
as a content-addressed `.previous.tsv`. Imported FEP scores or a non-MD frozen
selection require explicit reconciliation and are never overwritten. FEP imports
continue to use the existing frozen selection until the refresh. Other frozen
selections retain their existing semantics. Final MD failures supply no score;
the existing finite-elite minimum still governs next-cycle generation.


## Automatic Slurm node selection (2026-10-26)

**Confirmed user request:** make `processes`, `worker-hosts` and
`worker-ssh-known-hosts` unnecessary for COMBICHEM Slurm execution.
**Implementation choice:** without `worker-hosts`, request two nodes with 192
physical cores each, preserving the previous 384-slot default, and let Slurm
choose nodes from `slurm-partition`. Optional `slurm-nodes` and
`slurm-cpus-per-node` override these defaults; all nodes request the same 1..192
physical cores. This selects the requested number of nodes, not every node in the
partition or only currently idle nodes. Resources can queue normally. A newly
added cpu3 becomes eligible without changing hostnames in the TOML; using three
nodes requires `slurm-nodes=3` (576 slots), with sufficient available resources.

Preserve explicit `worker-hosts` for pinned allocations; derive resource counts
from that mapping and reject conflicting explicit Slurm counts. The two-node /
384-slot ceiling remains for direct SSH/local mode; Slurm accepts larger node
counts. Derive Slurm `processes` from node count times cores per node regardless
of an optional legacy processes value. Coordinator processes remain independent.
The known-hosts option applies only to direct calculator SSH, not native Slurm
calculator sockets. Submission-host SSH still uses the normal SSH configuration.

Submit one allocation without `--nodelist` for automatic mode. The native batch
launcher expands its actual SLURM_JOB_NODELIST, validates node count, unique safe
names and requested CPUs, creates private per-node authentication metadata and
atomically saves allocation.json bound to its JobId before starting pure pools.
The local coordinator waits for this record, verifies node/core/JobId/version
identity and then uses the actual mapping for slot assignment and cleanup.
Keep requested resources separate from discovered runtime hosts so readiness and
reattachment do not change the launch signature. Reject mismatched saved/native
identities; preserve existing tokens and never reuse another allocation's pools.
Legacy explicitly named launch records without the new resource fields remain
readable. Lost submission replies, uncertain history, completion verification,
local SQLite/journals, scientific fingerprints and existing result protection
remain unchanged. Node expansion is operational; the driver performs no science.
New source still requires a separate output; no live projects are reset or moved.


**Verification of automatic selection:** full suite 1,586 passed. Tests cover
omitting all three legacy options, default resources, three-node/576-slot requests,
explicit-host compatibility/conflicts, scheduler-selected cpu2/cpu3 metadata,
token preservation, identity rejection, stable signatures, reattachment without
new submission, and cleanup confined to discovered nodes. The real RDKit/TCP
pipeline also runs under the supervisor without worker-hosts, using the allocated
runtime map. Scheduler responses and native launch are mocked in allocation tests;
no new native Slurm job or production benchmark was run. Editable installation,
compileall, package/CLI imports and whitespace checks passed. The validated separate
examples/tmp/egfr-slurm-auto.toml omits processes/worker-hosts/known-hosts and uses
a new output; existing runtime TOMLs, allocations, databases and results are intact.

## Slurm full-product computation and bounded local claim owners (2026-10-26)

**Confirmed user request:** move per-product preparation and result evaluation
from tensor to Slurm nodes, and separate local worker concurrency from the remote
calculation slots. This supersedes the earlier local product assembly/reference
selection/property evaluation implementation choice below.

**Implementation choices:** `worker-hosts` and `processes` still describe the
remote calculation slots. Slurm-only `coordinator-processes` defaults to
min(16, local CPU count, remote slot count), with explicit values from 1 through
the remote slot count. Each independent local claim owner manages a disjoint,
balanced set of calculator slots through bounded socket-reader threads; threads
never access SQLite or launch/coordinate workflow workers. The driver still only
supervises. Claims, result journals, global duplicate/target admission, cycle
transitions and authoritative persistence remain local and SQLite-coordinated.

Send each claimed batch's immutable parent, full-precision pose history, plan,
ordinal range and deadline to a pure node calculator. On that calculator perform
fragment assembly, compatible reference selection, constrained search, descriptors,
filters, canonical SMILES and output pose/history serialization in the existing
order. Stream each product back in ordinal order, then require an explicit complete
batch reply with exactly the claimed ordinal range. Cache reference/evaluation
objects per calculator connection. Input property/bump evaluation is remote too;
local workers retain planning bookkeeping and final export. No node opens SQLite,
claims workflow work or writes scientific output files.

Local owners fsync received product journals and flush existing bounded buffers.
Network readers use bounded queues; stop/error prevents further claims and drains
already submitted batches, including after another reader fails. A failed reader
retains its unfinished claim and committed cursor; never record a transport error
as a scientific UNRESOLVED product. Persistence failures retain durable journals
for existing idempotent recovery before rearming. The supervisor's existing grace
limit still bounds draining; a forced termination can require recomputation of
unreceived results. A target stop can receive remaining products from already
submitted batches; preserve poses while SQLite enforces target admission.

Preserve seeds, atom order/stereo, original-core constraints, Morgan radius 2 /
2048-bit Tanimoto selection, full-precision coordinates, descriptor/failure rules,
and journal recovery. Fingerprint the extracted product calculator and new adapter;
new source requires a separate output, never bypass old guards or reset live jobs.
Reduced local process/DB-writer concurrency does not eliminate per-result NFS fsync
or establish a measured 384-slot throughput improvement.


**Verification of this change:** full suite 1,562 passed; final affected
regressions 242 passed (including 12 new pipeline regressions). These exercise
real RDKit calculations over authenticated local TCP node pools while forbidding
SQLite connections in the node processes, exact local/node report/pose/history
and descriptor equality, two simultaneous slots from one local owner, two frame
cycles, explicit supervisor owner counts, expired-deadline cursors, and recovery
from disconnects, missing batch completion and persistence failures. Editable
installation, compileall, package/CLI imports and diff whitespace checks passed.
No new native Slurm allocation or production throughput benchmark was run. The
separate examples/tmp/egfr-slurm-pipeline.toml uses 384 remote slots, 16 local
owners, batch32/buffer16 and a new output directory; it was validated, not launched.
Existing production inputs, DBs, jobs and results were not reset or moved.

## Slurm COMBICHEM calculation pools with local coordination (2026-10-26)

**Confirmed user request and correction:** replace direct cpu1/cpu2 SSH calculator
connections with Slurm; keep task allocation, SQLite and result storage on the
host invoking graft. Do not move the coordinator onto a compute node. This
supersedes the earlier two-host SSH execution choice for new Slurm runs. Glide
submission remains unchanged; no existing jobs/results may be reset or cancelled.

**Implementation choices:** optional `execution-backend="slurm"`, `slurm-host`,
`slurm-partition`, `slurm-time-limit` (seconds), `slurm-memory-mb` (MiB, zero=all
node memory), and `slurm-poll-seconds`. Require equal `worker-hosts` counts on one
or two named nodes (1..192 physical cores each, total <=384); omit execution-host.
The invoking host activates the shared remote-env-script LOCALLY so Python,
RDKit and NumPy match the nodes; its hostname, DB and supervision never move.
Legacy local/SSH inputs remain supported. Use a separate output for new code and
protocol; preserve frozen-source guards, claims, poses and result journals.

Submit one sbatch allocation for the whole computation, with native no-requeue,
physical-core requests (`--hint=nomultithread`), explicit memory and time bounds.
One native srun step starts a pure-calculator process pool on each allocated node;
never submit a scheduler job per molecule or open SSH sessions to the nodes.
SSH to the submission host is control-only (sbatch/squeue/sacct/scancel). Persist
unique job-name/JobId launch journals; reconcile lost submission replies using
both active and terminal history, and never resubmit uncertain/live allocations.
Slurm resource settings and session IDs are operational; scientific source/input
guards remain mandatory. Queue time precedes the cumulative max-seconds timer;
Slurm allocation time also includes initialization/export/cleanup.

Local independent workers atomically claim SQLite work, assemble candidate graphs,
choose references, perform ranking/filter bookkeeping and persist buffered results.
Send constrained force-field searches, bump/methyl probes and elite-MCS to native
node pools over authenticated internal TCP sockets, retaining full-precision
coordinates, stable seeds, atom histories and calculator versions. Relay MCS
checkpoint/log events back to the local worker for DB/log persistence. Nodes do
not open SQLite, claim workflow work or write scientific result files. Per-node
private endpoint/token files and native logs are allocation-control metadata only.
The local supervisor still performs no scientific calculations. Load a sidechain
library only in workers that actually claim a planning job, to reduce coordinator
initialization overhead. Original-core/sidechain protocols remain unchanged.

Retain the exclusive local stage lock and inherited fork lock before any session
recovery. Reattach a matching active native allocation after client interruption;
rearm local abandoned work only under existing lock/session rules. Pools alone,
zero exits or cached output never establish workflow completion: require local
DB/export completion and verified native parent/step completion before writing a
matching session completion record. A failed/disconnected calculator stops its
claim owner; retain computed result journals and committed results. Stop only the
current allocation's pure pools on normal/exception exit; a shutdown timeout may
request cancellation of that allocation, never unrelated production jobs.

**Verification:** full suite 1,551 passed; final affected regressions 230 passed.
The latter include readonly idle-frame waiting under an existing SQLite writer
lock. Native Slurm smoke JobId 133081 completed its parent, batch and multi-node
calculation step with exit 0:0. The coordinator was tensor.camd.krict.re.kr, with
one physical core requested on each of cpu1/cpu2 (3 allocated logical CPUs due to
cpu2 SMT). Two frame cycles completed 4+27=31 attempts, 30 new unique outputs,
no unresolved/clash failures and partial_search=false. MCS/probe checkpoint/log
streaming, atom histories, buffered local persistence and native cleanup completed;
a matching completed.json records the local coordinator. Node-pool tests deny all
SQLite connections and verify exact coordinate/history equality plus token
rejection. A 384-calculator production benchmark was not run.

The separate runtime input is examples/tmp/egfr-slurm.toml (192 cores per node,
48h calculation budget, 49h Slurm allocation, batch32/buffer16). Existing runtime
TOMLs/production outputs/jobs remain intact. The tracked replacement section is
examples/combichem-3d/slurm-settings.example.toml. Initialize/validate the local DB
before creating scheduler metadata; this preserves the dedicated-output guard.
Library loading is deferred to claimed plan work, and idle frame/plan/input waits
avoid SQLite writer locks. Coordinator activation stays local; no scientific
calculation or SQLite access runs on the Slurm submission host.

**SSH multiplexing repair (2026-10-26):** inherited `ControlMaster=auto`
and a common cpu2 ControlPath caused per-connection session refusals and control
socket warnings with many calculator slots. Force `ControlMaster=no`,
`ControlPath=none`, and `ControlPersist=no` for pure-calculator SSH connections.
Disabling ControlMaster alone still permits reuse of an existing master. Retain
serialized authentication startup, host-key verification and keepalives. Do not
remove shared sockets, change global SSH/sshd settings or interrupt existing jobs.
The repeated `remote calculator ready` messages establish successful calculator
initialization despite the preceding fallback warnings, not completed products.
The change applies to newly launched workers; running workers retain their loaded
implementation and source-fingerprint checks remain enforced on restart.

## Buffered COMBICHEM result persistence (2026-10-26)

**Confirmed user request:** reduce SQLite write contention by buffering results.
**Implementation choices:** `result-buffer-size=16` (1..256) and
`result-buffer-seconds=2.0` bound each worker buffer. Time is checked between
completed products, not by interrupting an ongoing scientific calculation. Flush
on size/time threshold, batch end, normal stop or caught exception. The separate EGFR
`examples/tmp/egfr-buffered.toml` uses `batch-size=32` to amortize claim/completion transactions too;
a batch size of one still forces a result flush after every product.

Before buffering a result, append/fsync its report, pose payload, history and
ordinal to a batch-owned JSONL journal in `<out-dir>/result-buffer/`. Commit a
bounded buffer in one SQLite transaction, including unique/target admission,
attempt counters, poses and cursors. Keep BUSY/LOCKED rollback/retry, WAL, foreign
keys and the 30-second busy timeout. Remove the journal only after commit.
On restart, after frozen-protocol validation and under the exclusive stage lock,
the supervisor launches one independent recovery worker before rearming claims.
It imports journals idempotently by parent/ordinal without recalculation, then
normal session recovery proceeds. A truncated final record is recomputed from
the saved cursor; malformed complete records stop recovery and retain the file.
Never reset live/uncertain jobs. The driver performs no scientific calculation.

SQLite remains authoritative for global target/count/state coordination. Progress
counters reflect committed results and can lag buffered computations. Concurrent
target completion still preserves all acquired poses but atomically limits new
eligible identities; buffering can do additional work before target detection.
Per-result journal fsync trades extra filesystem I/O for restart durability;
transaction count reduction is not a measured 384-worker throughput improvement.
Buffer implementation/source guards remain enforced; do not bypass old protocol
fingerprints. Existing live runs and stored outputs are not modified.

**Verification:** full suite 1,530 passed; buffered persistence/recovery tests
9 passed, including two final additions for supervised replay and atomic target
admission. A real cpu1/cpu2 artificial 81-combination run completed with six
result transactions (16,16,16,16,1,16 records) and no remaining journals. No
384-worker throughput claim is made. A live 384-worker production run was found
and left running. Keep its `egfr.toml` batch setting; the separately prepared
`egfr-buffered.toml` selects a fresh `combichem-3d-cpu1-cpu2-buffered` output for
an explicitly started buffered run. No production DB, claims or jobs were reset.

## Two-host COMBICHEM resources and contention recovery (2026-10-26)

**Confirmed user request:** cpu1 and cpu2 each provide 192 cores for COMBICHEM.
Support `worker-hosts = { cpu1 = 192, cpu2 = 192 }` with
`execution-host = "cpu1"`, for 384 single-threaded calculation slots in total.
Keep Glide on its existing submission path; the requested Slurm migration was
explicitly deferred. Default COMBICHEM execution remains local/min(16, CPU count).

**Implementation choice:** all SQLite-claiming processes remain on the execution
host. Slots assigned to the other host use persistent SSH pure-search helpers;
helpers never open SQLite, claim work, supervise stages or invoke other workers.
MCS, frame preparation and methyl probes remain with the original calculators on
the execution host. Shared source/input paths and identical Python/RDKit/NumPy
versions are required. Preserve seeds, atom order, stereo, atom histories and full
precision coordinates on requests and replies. Optional shared
`worker-ssh-known-hosts` resolves relative to the TOML and enables verified SSH
host keys. A transport failure stops the worker and retains its uncertain claim;
it must never become a terminal scientific UNRESOLVED attempt. Changed scientific
settings still require a separate output; never reset production jobs/results.

**Reported bug repairs:** retry rolled-back SQLite BUSY/LOCKED bookkeeping up to
eight times with jitter, retaining the 30-second busy timeout and the already
calculated result. Other SQL errors propagate. Idle claims avoid write locking
when all available batches are already running. Add a non-destructive per-parent
batch-state index. Separate saved `__worker_error__:PID` diagnostics from chemical
attachment sites in final analysis, retaining the original diagnostic rows.
These changes do not establish 384-slot production throughput or receptor accuracy.

**Verification (2026-10-26):** full suite: 1,522 passed; final SSH-adapter
regressions: 11 passed. Syntax/import and diff whitespace checks passed. A separate
artificial two-slot project finished all nine combinations. A direct cpu1-to-cpu2
RDKit search returned the same PASS pose/history as cpu1 with maximum coordinate
difference 0.0 (Python 3.12.15, RDKit 2026.03.1, NumPy 1.26.4). This validates
transport/coordination on a small artificial geometry, not receptor science or
384-slot throughput. The previous production DB was inspected read-only: 9,997
committed attempts (6,917 PASS, 3,080 CLASH_REJECT), three running batches retained;
final analysis now completes without the worker-error KeyError. No production run
was started and no old claims/results were reset. Distributed adapter files are
included in the frozen implementation manifest for new two-host runs.

## Elite-MCS frame cycles (2026-10-25; supersedes reference-parent/frontier growth defaults)

**Confirmed user correction:** reference parent ligands are pose references only,
not growing molecules. Start cycle 1 from the supplied reference-MCS alone,
retaining its 1–4 specified attachment anchors. Two anchors and K=10 yield 100
Cartesian attempts, not 24 reference-parent populations. Structure deduplication,
pose failures and output filters may reduce accepted molecules below 100.
Default growth-mode is now "frame-cycles". Preserve explicit "frontier" mode for
old synthetic/regression projects, never reinterpret or reset their checkpoints.

Prepare one bare MCS frame from a matching reference's actual retained subgraph,
cap cut valences with H and retain core coordinates. Reference substituents are
excluded. Each completed product selects its closest compatible reference pose
by Morgan radius-2/2048-bit Tanimoto for ORIGINAL reference-core coordinate
constraints; unmatched references never trigger unconstrained optimization.
The growing elite-MCS frame describes retained chemistry, while original reference
core atoms define fixed/restraint coordinates. Newly retained frame decorations
can relax under those original-core constraints. This is not docking/affinity scoring.

For cycle N+1 select up to elite-count=20 DISTINCT passing products from N only.
Calculate their MCS. If the concrete retained frame is chemically identical to N's
frame, halve the elite subset: 20→10→5→2→1. **Confirmed user change
(2026-10-26):** when reducing, sort the current subset by molecular weight and
discard the smallest molecules first, retaining the heavier half (floor, minimum
one). Initial elite selection still uses the configured QED/similarity ranking.
**Implementation choice:** calculate average molecular weight with RDKit MolWt
from the saved molecular structure, including hydrogens; equal weights retain
the original elite order. Reduced attempts use descending molecular weight,
including template selection; record weights alongside pose IDs in each attempt.
At one elite use its whole molecule as the frame. Equality uses canonical isomeric structure, not raw
SMARTS spelling. Reject/discard timed-out partial MCS and reduce the subset;
check the previous frame's retained UIDs, rings, formal charges and stereo.
Record attempts and template/elite IDs. If fewer passing products exist, use that
available ranked set; never substitute reference ligands or fabricate elites.

**Implementation repair (2026-10-26; reported CMPID_8/13):** saved SDF-derived
atom/bond traversal with chirality matching returned only 17 retained heavy atoms,
including a lone sidechain ring carbon, although both molecules share a 26-atom
stereocompatible subgraph. Normalize graph-only FMCS inputs through canonical
isomeric SMILES while retaining chirality/charge matching. Extract the frame from
the original template pose/UIDs, never from the normalized copy. Enable both atom
and bond CompleteRingsOnly, reject mappings cutting ring bonds, and verify the
concrete extracted frame matches every selected elite with chirality enabled.
Regression graphs reproduce the reported pair; this is graph/coordinate-preservation
evidence, not validation of receptor energetics or scientific ranking.

**Confirmed logging request (2026-10-26):** INFO logs each MCS attempt's previous
frame, complete selected elite ID/pose/SMILES/MolWt list, template, raw and readable
MCS SMARTS, extracted frame SMILES/atom count/status, and reduction kept/discarded
pose IDs. Persist input elite SMILES and weights in the checkpoint as well.
Timed-out partial MCS is discarded and never presented as an accepted structure.

**Resume diagnostics:** existing output is resumable only with the original frozen
inputs/scientific settings/calculator source. Fingerprint mismatch errors identify
changed manifest keys/files and explain using a new [combichem-3d].out-dir, or
--export-only for old results. Never bypass the guard or overwrite old calculations.

**Confirmed distance choice:** molecular bond distance, not Euclidean distance.
On newly retained sidechain atoms, find replaceable terminal H directions and
independently test methyl probes with only the original iteration-0 core fixed;
all sidechains may relax jointly.
For every PASS heavy-atom site compute the MINIMUM shortest bond-path length to
any previous attachment atom. Select up to next-attachment-count=3 with the
per-initial-branch balancing/cycle-cap policy below; within a branch prefer greatest
such distance and break ties by stable UID. One logical site per H-bearing heavy
atom, including ring C-H/N-H, not one per individual H. This definition of terminal
H preserves earlier elemental/H substitution rules; it does not restrict heavy
atoms to graph degree one. If fewer than three pass, use those available; if none
pass, preserve the explicit clash/unresolved evidence and stop. Real fragments
replace H directly, followed by a fresh joint constrained search and bump checks.
Only selected sites form the full next Cartesian product, including later-cycle -H choices.

Cycle creation, ranked selection, frame preparation claims and checkpointing are
SQLite-coordinated. Calculators perform elite MCS/probes, never coordinate workers.
One immutable frame per cycle; products are not independently queued as parents.
Persist MCS/probe checkpoints and cycles.jsonl; restarting under the exclusive
stage lock rearms abandoned local work without losing poses or repeating completed
MCS/probes. Keep old results, jobs and frozen protocol guards intact.

Use stop-mode="cycles" (the new frame-mode default when not explicitly overridden)
to finish each configured cycle within max-seconds. A legacy target-count=10
would otherwise cut the first 100-product space short and cannot supply 20 elites.
Explicit target/time modes retain their partial-search behavior; do not silently
claim full-cycle completion when interrupted. Changed growth protocol requires a
new output. Legacy root driver/Glide/GBSA behavior is unchanged.

**Confirmed elite criterion:** the user selected QED. Set elite-rank-by="qed"
as the default, descending, with compound ID then pose ID as deterministic ties.
Rank distinct passing molecules from the immediately preceding cycle only. QED
is not binding affinity. Reference similarity remains an explicit alternative;
external Glide/GBSA scores must not be fabricated or copied from reference ligands.

## Optional later growth, joint sidechain relaxation and atom cycle tags (2026-10-26)

**Confirmed user changes:** enumerate partially substituted products at later
frame cycles, optimize old and new sidechains together with only the original
iteration-0 core fixed, and expose each generated atom's attachment cycle.

At frame cycles >=2 add -H to every selected site's saved candidate pool if absent,
without removing any sampled heavy fragment. Keep cycle 1's configured Cartesian
product and explicit frontier behavior unchanged. With K=10 heavy fragments and
three selected sites enumerate 11^3=1331 combinations: 30 single-site, 300 two-site,
1000 three-site and one all-H no-op. Existing H-enabled pools already reserve an
H slot within K and must not duplicate it. Preserve all combinations, mixed-radix
cursors, masks and atom histories in SQLite; do not silently drop all-H. It is the
existing frame's identity, not a new unique compound. Only non-H additions consume
a root growth cycle. Record expanded sites/count in each attempt and summarize
mask counts in the final log. Every attempted combination retains normal constrained
search and bump/filter gates. Active-root MCS acceptance remains mandatory.

For frame-cycle methyl probes remove the parent-heavy-fixed-first phase: local
H-direction starts and constrained ETKDG/torsion searches all permit previous and
current sidechains to move. The fixed/restraint map contains only the original
history.core UIDs, never the enlarged frame_uids. Actual products already use that
joint map and retain it. The EGFR input explicitly sets core-constraint="fixed";
other explicit restraint settings and opt-in legacy frontier probe behavior remain
unchanged. Keep core displacement, stereo, receptor/internal bump checks and the
100-iteration capped-minimization policy. Record constraint_scope in search reports.

Add public integer atom property graft_added_cycle from saved added_iteration:
original core=0, generated fragment atoms=their addition cycle. Preserve these
values through trimming, no-ops, assembly and restoration; never relabel old atoms
with the current cycle. Export atom.iprop.graft_added_cycle in both SDF files
using RDKit integer atom-property lists, which restore atom properties on read.
Hydrogen birth is not tracked: leave it missing (n/a), and retain any INFERRED
history provenance/assumptions rather than promoting inferred chronology to fact.
Changes require a new output; existing jobs and scientific results remain intact.

## Per-initial-attachment growth caps, balanced selection and final log analysis (2026-10-26)

**Confirmed user requirements:** configure max growth cycles per initial attachment,
select later attachment points fairly across original branches, and append a complete
cycle/frame/attachment analysis to the end of run.log. Show attachment positions in
the requested CH*/c* display convention with 1-based frame atom numbers, alongside
ordinary computational SMILES. Display notation is never used for chemistry.

**Confirmed input simplification (2026-10-26):** in frame-cycles, positive dummy
map values in the initial reference-mcs encode maximum growth cycles directly.
For `c1nc(N[*:2])c2cc(-c3ccc([*:1])cc3)[nH]c2n1`, the N branch grows at most twice
and the phenyl branch once. `[*:999]` and an unnumbered `[*]` mean unlimited.
Repeated values are valid: two `[*:2]` sites remain two distinct two-cycle roots.
Assign internal root IDs 0,1,... by initial dummy input order, independently from
map values; persist that mapping, input values and limits in each history and log.
Global max-iteration/time bounds still apply. This supersedes the separate limit
setting as the recommended input for new runs.

**Compatibility interpretation:** retain the previous nonempty
`initial-attachment-max-cycles` map/string as a legacy ID-label input. A SMARTS
containing explicit `[*:0]` also retains the legacy label interpretation, preserving
old :0/:1 artificial/explicit projects; in that mode map numbers do not impose
cycle caps. Reject duplicate/absent IDs in legacy mode. Do not reinterpret old
saved histories or bypass fingerprints. Use positive dummy values and omit the
legacy setting for the new notation. Legacy root/Glide/LibInvent dummy semantics
are unchanged. A growth cycle counts once
per original branch with an actual non-H addition, even for multiple simultaneous
sites on that branch. -H no-ops and probes do not consume saved growth cycles.
Persist original-label/branch mapping, growth counters and cumulative selected-site
counts in pose histories, through MCS extraction and restart. Reject assembly and
skip methyl probing/selection on exhausted roots. Preserve capped branches' existing
chemistry. Stop explicitly with attachment_cycle_limits_reached when candidate
roots are exhausted; this is an expected configured stop, not an unresolved search.

**Confirmed active-root acceptance change (2026-10-26):** a chemically expanded
MCS is insufficient when it removes all structural follow-up growth sites from a
root whose growth budget remains. For histories with per-root cycle-limit metadata
(including new map-cycle 999/unlimited roots), require at least one newly retained,
H-bearing heavy atom per still-active initial root, using the existing terminal-H
candidate definition. Exhausted roots do not require follow-up sites. Preserve
uncapped legacy empty-limit histories' previous MCS behavior. This is a structural
acceptance condition; methyl probing and finite clash searches remain separate.

Reject such MCS attempts as missing_active_root_sites, record required/missing
root IDs and per-root candidate counts, and continue the same molecular-weight
subset reduction. Consider all bounded ring/stereo-compatible template mappings
before concluding that an active root is missing. At one elite use the whole
molecule and apply the same condition. If it still cannot supply a required site,
stop explicitly with required_growth_sites_unavailable, retaining checkpoints and
results; do not loop forever, fabricate a frame, or label this a completed growth
budget. Report the rejection evidence in live and final log analysis. Do not
consume growth counters during these checks. The reported 65/39 pair now rejects
its 25-atom MCS and reduces to CMPID_65's full 31-atom structure, which retains
three structural N-branch candidates; actual bump eligibility requires calculation.

**Balanced selection interpretation:** among eligible probe-PASS sites, prioritize
roots with fewer slots selected in the current cycle, then fewer cumulative slots,
then the root's best remaining shortest-bond distance, then numeric root label.
Within a root retain descending bond distance and UID ties. This gives each active
root one slot before assigning extras when capacity permits, rotates extras, and
fills unused slots from other roots when a root has insufficient passing sites.
Keep full K^m product enumeration and all existing constrained search/bump gates.
This supersedes the former global distance/UID top-N selection; do not rewrite old
selections or apply new caps retrospectively to old saved results.

Read all final analysis data from one read-only SQLite snapshot; all SQL stays in
database.py. Append totals and per-cycle counts/property ranges/convergence/errors,
frame SMILES and site-marked forms, MCS subset reduction and selected elites,
PASS/CLASH_REJECT/UNRESOLVED sites with distances, initial root, selection and
limit-exclusion evidence. Show legacy selection policy and missing counters honestly
when analyzing old runs. Rendering/logging does not initialize or mutate scientific
state. Future successful executions append this block after export; old completed
run.log may receive it on explicit user request. New policy/source/settings require
a new output folder, retaining old results and source fingerprints.

## Bounded force-field relaxation before bump checks (2026-10-26)

**Confirmed user change:** the EGFR COMBICHEM 3D project now sets
optimization-iterations=100. Stop each MMFF/UFF minimization at that iteration
limit; incomplete convergence alone must not make a candidate UNRESOLVED.
RDKit Minimize return code 0 records convergence; code 1 records iteration_limit
and continues with the resulting coordinates. This applies to methyl probes and
real products. Other return codes/exceptions, missing parameters, nonfinite
energy/coordinates, core displacement and stereo failures remain genuine errors.
All eligible poses still require receptor/internal bump and output-filter checks.
No pose passes merely because minimization ended. Record convergence, return code,
configured iteration limit and aggregate optimization status counts in attempt/probe
reports; preserve nonconvergence explicitly rather than labelling it converged.
This limit counts minimizer iterations, not seconds; it is separate from the
project's 48-hour cumulative compute budget. Other explicit iteration settings and
legacy scientific workflows are unchanged. Existing results/fingerprints remain
immutable; a changed protocol requires a fresh output. Tests verify return-code
handling, geometry/bump gates and durable reporting, not real-receptor quality or
an empirically measured speed/acceptance improvement.

## Bounded random sidechain pools (2026-10-25)

**Confirmed user change:** restrict attached fragments to 5–10 atoms and select
10 random candidates, with the count configurable in the input TOML.
**Implementation interpretation:** count heavy atoms only (atomic number >1),
excluding attachment dummies and explicit/implicit hydrogens, consistent with
existing sidechain size controls. New defaults are sidechain-min-heavy-atoms=5,
sidechain-max-heavy-atoms=10, sidechains-per-site=10, sidechain-selection="random",
sidechain-include-h=false. The -H parser remains supported; explicitly enabling
it reserves one of K slots for -H, leaving K-1 sampled heavy fragments. Explicit
site pools must also obey size eligibility and K; too few eligible candidates
produce a clear error rather than silent pool shrinkage. The artificial toy keeps
explicit ordered/H-enabled controls and its original small fragments.

Sampling without replacement is reproducible from seed and atom UID, independent
of worker order and library row order (sample sorted canonical SMILES). Only
candidate pools are sampled: still enumerate their full K^N/product(N_i) spaces,
subject to recorded time/count termination. Save actual pools in durable plans
and include size/sampling settings in the frozen scientific manifest.

**Historical implementation interpretation (frontier/target mode only):** interpret the repeated
"limit to 10" as a separate final-new-unique-compound cap as well. The default
3D target-count is 10; the copyable scoped settings explicitly override legacy
root target-count/stop-mode and any legacy sidechain-max-heavy-atoms=8. Use
stop-mode="target" for the cap; time mode retains its existing continue-to-time
meaning. Admission of new passing identities is capped inside the same SQLite
transaction as result persistence, so parallel completions cannot overshoot.
Preserve surplus valid geometry/properties/history with output_passed=false and
target_count_limited evidence; never relabel them as clashes or discard them.
Imported identities and alternate poses do not count as new unique compounds.
Changed sampling/size settings require a separate output, preserving old results.

## COMBICHEM 3D execution logs (2026-10-25, revised for concise structure summaries)

**Confirmed user change:** default logs emphasize iteration input frames and
result structures. INFO prints bounded FRAME previews (CMPID, pose, parent SMILES,
core SMARTS), periodic attempted/unique counts and remaining time, then per-iteration
PASS unique/pose totals, CLASH_REJECT/UNRESOLVED counts and RESULT SMILES previews.
`log-structures` controls preview size (default 3, range 0–20); complete inventories
stream to `iterationNN-structures.jsonl`, retaining parent links and output_passed
flags. Iteration imports/baselines are labelled with their source iteration;
valid saved geometry poses are distinct from filter-passing output structures.
Full pose coordinates and growth histories remain in the existing SDF/JSONL files.

`--debug` / `log-level="DEBUG"` adds compact per-parent plans, baseline counts and
worker events, without per-product starts or repeated optimization-error lists.
Only `--trace` / `log-level="TRACE"` requests verbose per-probe/product diagnostics.
`log-interval` defaults to 10 seconds. Timestamp/PID-tagged logs flush to stdout
and append to the dedicated output `run.log`. The legacy root debug chemistry
setting is unchanged. Log controls are operational; calculations, failure
classification, exhaustive enumeration and saved scientific data are unchanged.

## Approximate imported history (2026-10-25, supersedes earlier no-inference rule)

**Confirmed user change:** when the existing project TOML omits `history`, infer
imported ancestry from parent structure and `reference-mcs`. Shared-project mode
defaults `infer-history=true`; standalone mode defaults false. Explicit history
always wins, including errors for missing/invalid explicit files. Users may set
`infer-history=false` to retain strict explicit-history requirements.

Remove terminal attachment dummies from the query while retaining their core
anchor correspondence. Enumerate up to 4096 chirality-compatible core matches,
choose the lexicographically smallest mapped atom-index tuple, and record match
count, ambiguity, cap and the selected mapping. Core coordinates are the mapped
parent pose coordinates; no alignment or minimization occurs. Infer each connected
component of non-core heavy atoms as a fragment/branch; record its core contacts,
including bridging/multi-contact components. These are approximate graph groups,
not recovered chemical synthesis fragments. Default core birth is 0 and all
non-core components are assumed born at `inferred-parent-iteration=1`; changing
that positive integer changes the assumed imported parent iteration, not evidence
about its real history. Downstream additions retain exact generated ancestry.

Store `provenance.status=INFERRED`, method, assumptions and ambiguity in each
history and preserve it through descendant/output histories. Missing core matches,
invalid parents or unsupported attachment count fail without dropping records.
Persist `input-cache/inferred-history.json` atomically; incompatible inferred
history/source changes stop resume rather than overwriting saved results.

## Shared project TOML inputs for COMBICHEM 3D (2026-10-25)

**Confirmed user change:** `graft combichem-3d` accepts the existing project
input TOML. Reuse shared receptor/parent/library/reference inputs and append
3D-only controls under `[combichem-3d]` and `[combichem-3d.filters]`, using the
control names from `examples/combichem-3d/toy-inputs/grow.toml`. Explicit section
values override shared values. Resolve every local path against that input TOML.
Keep standalone 3D TOMLs working and reject unknown 3D section keys.

**Confirmed native input change:** parent poses come from `reference-pose`
(`.mae/.maegz` supported), and the fixed receptor comes from the unique
`*_recep.mae` member of `dock-grid`. Explicit `[combichem-3d].parents/receptor`
overrides remain supported. Export through the installed Schrödinger Python,
without docking, minimization, hydrogen addition or coordinate alignment. Keep
original files unchanged; validate parent count, atom order/identity/charge,
bond count and coordinates, and receptor atom/element/residue/coordinate
preservation. PDB rounding and native HID/HIE/HIP→HIS residue naming are allowed;
record original native identities/coordinates in `atom_source_map.json`.
Reject absent/ambiguous grid receptors and multi-structure receptor members.
Only explicitly marked `b_glide_receptor` pose-viewer records are excluded,
with their source indices recorded. Cache exports atomically under the separate
3D output `input-cache/native-inputs`, freeze source/helper hashes and verify
cached file hashes on resume. Changed inputs never overwrite saved results.
`schrodinger` / `SCHRODINGER` selects the local suite; no docking jobs are submitted.

Reuse `sidechain-library`. Explicit `reference-smiles` wins; otherwise read
iteration-0 reference SMILES from the original project DB read-only, without
migration/claims, or use the exported reference-pose ligands if no DB exists.
Default output remains `<project out-dir>/combichem-3d`, with a separate DB.
Explicit authoritative history takes precedence and must match exported atom
indices. Without it, use the user-authorized INFERRED import protocol above.
Native structures alone never establish the actual synthesis chronology. The existing reference-mcs may supply only a
missing history query. Copy additional controls from
`examples/combichem-3d/project-settings.example.toml`; paths resolve against the
real project TOML, never the toy folder. Legacy supervision/jobs are unchanged.

## Opt-in COMBICHEM 3D pose growth (2026-10-25)

**Confirmed user requirements:** implement `graft combichem-3d growth.toml` as
an independent local workflow with prepared fixed receptor PDB/SDF, aligned 3D
parent SDF, explicit Reference-MCS atom/coordinate mapping, authoritative atom
UID/branch/fragment/birth history, a single-ended fragment library including -H,
and property/similarity reference SMILES. The original no-inference requirement is superseded by the approximate-import
protocol above; retain its uncertainty labels. Default absolute max iteration is 1. Imported
iteration-1 parents can start growth at 2. Support 1–4 initial attachment atoms.

Iteration 1 enumerates the full configured core Cartesian product K^N; core
methyl probes there are diagnostic, not a replacement for checking each product.
At N>=2 find H-bearing heavy atoms across every fragment added at N-1, replace
one H per atom, and enumerate product(N_i) over all probe-PASS sites together.
Keep old terminal atoms, internal bonds and specified stereochemistry. Retain all
new fragments as the next frontier; no same-iteration recursive expansion, mN
single-site shortcut, symmetry-site deletion, beam search or hidden sampling.
-H is an explicit no-op choice; final chemical identities deduplicate by isomeric
SMILES while separate poses/frontier histories remain available.

For each site, independently try a methyl probe with parent heavy atoms fixed,
then rescue with only the explicit Reference-MCS constrained. Use constrained
ETKDG/torsion starts and MMFF/UFF; distinguish exact fixed points from finite
position restraints and check final maximum core displacement and stereo. Do
not freely rotate ring/amide bonds. PASS requires a valid clash-passing pose;
CLASH_REJECT only describes the configured finite valid search. Embedding,
parameter, optimization and insufficient-pose failures are UNRESOLVED, never
clashes. Record baseline clashes separately. Assemble real products from the
unprobed parent, and repeat joint constrained search plus receptor/internal bump
checks; independent passing probes never imply multi-fragment compatibility.

The fixed-receptor checker uses spatial indexing and radius-sum overlap, explicit
count/tolerance controls, 1–2/1–3 exclusions and configurable 1–4 policy. Water,
cofactor/metal and H policies are explicit. Ligand force fields contain no receptor
repulsion. This is MCS-constrained sidechain conformational search + receptor
clash filtering, not docking or affinity prediction. No external docking engine
is required. The radius model does not validate H-bond/metal coordination physics.

Use a separate output/SQLite DB. Independent local calculators atomically claim
bounded parent/product batches; the supervisor only supervises and keeps iterations
sequential. Preserve WAL, foreign keys, 30-second timeout, CMPID identity,
per-combination commits, big-integer cursors, all poses/history and failure/deferred
records. Never materialize the full product. Up to 16 processes, seed, search and
filter settings, output-vs-intermediate pruning, 30-minute default compute budget,
target/time stopping and restart behavior are configured explicitly. Save partial
search evidence on limits/Ctrl+C; never reset live calculators. Existing legacy
Glide/GBSA workflows, projects, results, native jobs and defaults are unchanged.

**Implementation protocol and limits:** `combichem3d_v1` uses heavy-atom UID history,
keeps supplied and optimized H coordinates in poses, and stores input/source/runtime
fingerprints. Artifact formats, complete input history convention, configuration,
minimal dependencies, commands and runnable artificial inputs are documented in
`docs/COMBICHEM_3D.md` and `examples/combichem-3d/`. Artificial RDKit tests include
individually passing probes with colliding real fragments. They do not validate
real-receptor accuracy or 10k–100k/30-minute throughput on 16 cores/64 GB.

## Readable SMARTS lines in MCS logs (2026-10-25)

**Confirmed user request:** immediately follow each `MCS SMARTS` and
`MCS SMARTS summary` line with an additional readable SMARTS representation.
For display only, relax the generic ring predicates as in the existing summary
and render simple atomic-number queries using element symbols, e.g. `[#6]` as
`[C,c]`, retaining both aromatic and aliphatic alternatives. Preserve complex
atom conditions, isotope/charge/chirality/map annotations and connectivity.
Keep the original lines, computational SMARTS, saved snapshots, files and all
scientific matching/docking/generation behavior unchanged.

## Return deleted populations to Generate during supervision (2026-10-25)

**Confirmed user request:** when the current generated iteration has no compounds
because its population was deleted, return from a later pending stage to Generate.
Check at stage boundaries and after downstream pending workers; rebuild the normal
sequential stage queue for that same iteration. Recognize deletion using the
existing zero-row plus durable prior-population evidence predicate. A nonempty
population with zero druglike passes does not trigger regeneration. Preserve
`--once` behavior and iteration-0 reference scheduling.

The driver only redirects independent workers. Generate owns all cleanup through
the existing stage locks, native-job reconciliation, archived iteration directory,
SQLite/WAL backup and transactional metadata reset. Unknown/live jobs, partial
deletions, missing ownership and later dependent compounds retain their blockers.
A frozen MD selection keeps its recovery ownership and prevents automatic rerouting
or reset even if its compounds were deleted. No scores, native jobs or other
iterations are reset by the supervisor. Existing supervisors need a graceful
restart to load this driver change.

## MD-GBSA readiness before source-file preparation (2026-10-24)

**Implementation repair for the reported iteration-7 failure:** before preparing
new MD work, check persisted upstream completion and require at least one finite
Prime result in an eligible completed state. Share the eligible-state/finite-score
predicate with the atomic selection transaction. Check before opening/hashing the
Prime complex file, uploading helpers, remote compatibility checks or any launch.
The supervisor reports the same eligibility blocker for an unsaved MD selection.

An iteration with all compounds rejected by drug likeness or failed upstream
remains blocked with an explicit no-eligible-Prime-results reason, rather than
raising a misleading missing-coordinate-file traceback. Do not create an empty
selection, mark MD complete, relax filters, regenerate compounds or advance the
iteration. Enough valid preceding elites remain mandatory for new generation.
Existing saved MD selections bypass this new-work preflight and retain native
recovery, immutable membership and protocols even when current eligibility or
configuration changes. Eligible new work still requires the verified final Prime
coordinates and existing compatibility checks; SQLite rechecks readiness and
freezes/claims the ranking atomically after compatibility validation.

## Combichem timeout rejection and continued halving (2026-10-24)

**Confirmed user change:** for new automatic ranked combichem snapshots in
iteration N>1, reject a timed-out MCS attempt and continue with the highest-ranked
floor half, including `2 -> 1`. At one elite, use the existing validated terminal
sidechain replacement and actual per-compound scaffold provenance. This
supersedes stopping on timeouts in the earlier ranked-elite and search-budget
policies; it does not change the configured per-search timeout.

Distinguish RDKit cancellation using `MCSSearchTimeoutError`, a `RuntimeError`
subclass. Discard the partial MCS completely. Record `status=mcs_timeout`, subset
IDs/count, `timeout_seconds`, failure message and `mcs_smarts=null` in the attempt
history of the accepted immutable snapshot. Log the rejection while searching
and its timeout/discard evidence on creation/resume. New ranked snapshots use
`elite_mcs_policy=ranked_halving_v2`; saved v1 snapshots remain unchanged and are
never recomputed to adopt this change.

Invalid/empty MCS results and unrelated failures still stop. Reference/iteration-1
inference, explicit/fixed cores and LibInvent behavior retain their error policy.
Preserve all preceding/fixed core, attachment and decomposition validation,
completion gates, scores, saved cuts and native jobs. A timeout rejection does
not establish a maximum MCS or scientific accuracy of the resulting compounds.

## Configurable MCS search budget (2026-10-24)

**Implementation repair for the reported iteration-7 timeout:** expose
`mcs-timeout` in the project TOML as a positive integer in seconds (default 60),
rejecting booleans, floats, strings and nonpositive values. Apply the configured
budget to each new reference/elite MCS search; do not overwrite it during runtime
initialization. The former timeout error named unsupported CLI flags; report the
TOML setting and supported explicit generation-core override instead.

Log each ranked combichem subset size, its search budget and rejection/acceptance
before continuing, so a completed all-elite search cannot conceal a later subset
timeout. Chemical validation remains unchanged. Canceled searches never use
their partial MCS; the later confirmed combichem timeout policy above permits
ranked subset reduction, while other inference paths still stop.
Saved scaffold snapshots, scientific results and native jobs remain unchanged.
New workers reload the TOML budget; already running searches keep their budget.
A longer timeout does not guarantee completion or establish scientific accuracy.

## Configurable positive MD snapshot count (2026-10-23)

**Confirmed user request:** remove Graft's artificial 20–100 snapshot limit.
`md-gbsa-snapshots` accepts any positive integer, with default 20 and no configured
upper bound. Reject zero, negative values, booleans, floats and strings. This is
Graft validation, not a Prime minimum-size requirement.

For two or more snapshots, preserve distinct evenly spaced frames in the configured
production analysis window. For one snapshot, select the final production frame.
Still reject incomplete trajectories and requests exceeding available distinct
frames. A single observation stores its energy as the mean and `std=0.0` as an
explicit storage convention; this does not estimate sampling uncertainty. With
multiple observations (including replicas), keep the sample standard deviation.
Apply identical statistics in native collection, ensemble merging and DB validation.
Default protocols, existing immutable selections, deployed remote helpers, live
jobs and scientific results remain unchanged; new counts apply to new selections.

## Restart recovery of missing final-iteration GBSA scores (2026-10-23)

**Confirmed user request, clarified to resume only the final calculation:** on
restart, resume the latest existing iteration (including 0) at its last entered
GBSA stage. A frozen MD selection or even one finite MD-GBSA score establishes MD
as that stage. Do not backfill any missing Prime scores in that cycle, including
those of other compounds without MD scores; retire stale Prime retry intents.
Only missing selected MD results are retried. Otherwise resume Prime or Amber if
already entered; retain the normal full sequence for a new iteration.

When Prime is the last stage, retry membership follows its final configured top-N
(all references for iteration 0). MD membership stays in the immutable saved
selection. Keep native recovery of earlier live/uncertain submissions and claims;
skip earlier completed workers. Respect disabled Prime and explicitly requested
other iterations. Unselected compounds and upstream failures are not new targets.
Existing successful scores remain intact. Earlier-cycle MD results do not disable
Prime for a newly entered cycle. This clarification supersedes retrying both
Prime and MD failures after the current cycle has already entered MD.

The supervisor records durable SQLite retry intents only for terminal failures in
the corresponding stage, once per restart. Existing pending/running work follows
normal recovery; workers alone verify and rearm failed calculations. An unfinished
intent gates stage completion and is resumed after a crash, without generating a
new retry on every worker poll. A repeated terminal failure waits for another
supervisor restart. Missing/ambiguous JobIds or any live/unknown native parent or
child block reset for reconciliation.

Workers preserve old failure evidence and native journals. MD retries archive only
the failed phase with a durable, idempotent move plan. A failed snapshot Prime job
reuses completed MD and prepared frames; failed MD or trajectory analysis reruns
MD using the same frozen protocol. Successful replicas and other compounds remain
unchanged. Legacy frozen remote helpers need no replacement. This recovery policy
supersedes terminal failures being permanently skipped on supervisor restart; it
does not establish scientific validity of a repeated calculation.

## Combichem ranked-elite MCS reduction (2026-10-23)

**Confirmed user request, superseding the earlier automatic combichem fallback:**
for each new automatic combichem iteration N>1, sort the selected N-1 elites by
its active score, then ID. Infer the MCS of all elites first. If it does not
safely extend the preceding generation core, repeat on the highest-ranked floor
half, stopping at one: e.g. `10 -> 5 -> 2 -> 1`, or `7 -> 3 -> 1`.

A multi-elite candidate must have strictly more retained heavy atoms and contain
the previous retained core. Compare chemical graphs after removing attachment
dummies, independent of atom maps, serialization order and SMARTS ring-query
spelling. Require valid terminal single-bond attachments, the fixed docking core,
and successful decomposition of every elite in that candidate subset. A valid
extension is accepted immediately; otherwise halve again. Non-single boundaries
and zero attachments reject that attempt. Invalid MCS errors still stop.
The later confirmed timeout policy above rejects canceled attempts and halves
again, without using an incomplete search.

At one elite, use the existing terminal-sidechain replacement protocol rather
than a whole-molecule MCS. Preserve actual per-compound scaffold provenance for
this parent and the existing saved-cut/empty-batch recovery. Missing/incompatible
history still stops. This does not guarantee MW or ROT increases every cycle.

The configured elite count is unchanged; only the MCS input prefix shrinks.
Persist the complete ranked ID list, actual subset IDs, each attempt's count,
SMARTS and outcome, and the accepted scaffold in the immutable iteration
snapshot. The original policy recorded `elite_mcs_policy=ranked_halving_v1`;
new snapshots use v2 under the confirmed timeout-rejection policy above.
Log the reduction on creation/resume.
Explicitly supplied/fixed saved cores, iteration 1, mol2mol/LibInvent behavior,
prior restarts, docking protocols, scores/results/jobs and all previously saved
iteration scaffolds remain unchanged. Apply this policy only to new snapshots.

## Optional Prime and Glide elite mode (2026-10-23)

**Confirmed user request:** support strict boolean `amber-gbsa = false` and
`prime-gbsa = false`, and select elites using Glide when Prime is disabled.
Prime defaults to true. `amber-gbsa` aliases `amber_gbsa` and the legacy
`amber-gbsa-enabled`; conflicting aliases are errors. `md-gbsa=true` requires
Prime enabled for new MD work; the current combichem project disables MD too.

With Prime off, finish the full generation/druglike/LigPrep/Glide population,
retire saved upstream submissions and require enough finite docking scores.
Select exactly the preceding iteration's lowest finite Glide scores, breaking
ties by compound ID. Continue to use `prime-gbsa-elite-count` for the elite count
(default 10); it is not a requirement to calculate Prime. New generation snapshots
record `elite_score_source=glide`; saved snapshots retain their selection source.
LibInvent still starts TL from the original prior, validates decomposition and
vocabulary, and exports a distinct `glide_score` column. Never write docking
scores into Prime/MD score columns. Terminal downstream GBSA failures do not
invalidate a retained finite final docking score in this explicitly chosen mode.
The docking constraint and constrained SP pose selection remain unchanged.

The driver omits disabled new Amber/Prime work, including unscored reference
calculations, but recovers existing Amber manifests, Prime submissions/claims,
and frozen MD work before advancing. Disabling a flag never cancels native jobs
or deletes results. Uncertain/orphan claims remain blocked for reconciliation;
no automatic reset. New FEP exports use docking scores in Glide mode, with no
unfinished-Prime requirement; existing frozen FEP selections/imports are retained.
Restart an existing driver once after changing Amber/Prime switches; MD's existing
runtime opt-in handling remains supported. This mode explicitly supersedes the
GBSA-required generation baseline when `prime-gbsa=false`; default GBSA behavior
remains unchanged. Glide ranking alone is not validated as an affinity estimate.

```toml
amber-gbsa = false
prime-gbsa = false
md-gbsa = false
prime-gbsa-elite-count = 10
```

## Automatic combichem boundary recovery (2026-10-23)

**Implementation repair for the reported blocked run:** an automatically inferred
MCS can end at a carbonyl carbon, placing a C=O double bond across the generation
boundary. Never convert that bond to a single bond or discard the offending
elite. For a new automatic combichem iteration N>1 only, reuse exactly N-1's
saved scaffold through the existing validation: valid terminal single-bond
attachments, containment of its generation core and the fixed docking core,
and successful decomposition of every selected elite. Missing/incompatible
history still stops. Explicitly supplied cores and iteration 1 still reject
non-single boundaries. Record `fallback_reason=non_single_attachment_boundary`,
attempted MCS, preceding iteration and current elite IDs in the immutable snapshot;
log the fallback on creation/resume. Existing snapshots, results, jobs and
scientific bond orders remain unchanged. This execution repair does not establish
that the resulting scaffold improves target affinity.

## Optional Desmond / Prime MD-GBSA (2026-10-21)

**Confirmed user requirements, implemented:** strict boolean TOML `md-gbsa`
(default `false`) enables `prime_gbsa -> md-gbsa -> FEP` for generated iterations
and `prime_gbsa -> md-gbsa` for iteration-0 reference compounds.
Without this flag, legacy scheduling/ranking remains unchanged. Saved MD work is
recovered even if the flag is subsequently disabled; an existing frozen MD
policy continues to govern that iteration's downstream selection.

**Iteration-0 scheduling repair (2026-10-22, user-requested):** references also
run optional MD after all reference Prime calculations retire. Apply the same
finite-Prime ranking and input ratio/count (default best half), using final
optimized Prime complexes. References with imported finite Prime scores are
eligible. No generation, LigPrep, Glide or FEP is added to iteration 0. The
supervisor completes pending reference MD before advancing to generated work;
saved selections recover even with the flag disabled. Preserve all scores,
reference identities, native jobs and frozen protocols. Existing drivers must
restart to load this repair; no running process or job is reset automatically.

After the final Prime candidate set and all saved upstream jobs retire, freeze
finite completed Prime results in ascending score/ID order: the best half
(rounded up), or `md-gbsa-input-count` when supplied. Freeze ownership, protocol,
remote paths, source/helper hashes and candidate IDs in one SQLite transaction.
Do not refill failures or change this selection when TOML/ranking changes.
**EGFR configuration update (2026-10-22, user-requested):** both active EGFR
project TOMLs explicitly set `md-gbsa-input-count = 20`, overriding the 50%
ratio. Select the best 20 finite eligible Prime results (all if fewer than 20).
This applies to new selections, including iteration 0; frozen selections and
submitted work remain unchanged.
Use the published final optimized per-ligand Prime receptor/ligand pair, preserving
`CMPID_<id>` and original atom IDs, bonds, ligand identity and ions/metals/cofactors.
Unsupported parameters or identity changes fail that compound; no silent removal.

**Implemented scientific protocol, not a user-validated accuracy claim:**
OPLS4, SPC water, orthorhombic 10 Å buffer, neutralization and 0.15 M NaCl;
installed Desmond standard NPT relaxation (160 ps in the verified installation),
then configurable additional equilibration (default 1 ns), then separate
unrestrained NPT production (300 K, 1.01325 bar; default 5 ns, configurable to
20 ns or other positive durations). One replica by default; replicas use distinct
seeds. Select a configurable positive number of distinct frames (default 20,
no fixed upper bound) from the final 80% of production, excluding equilibration.
For two or more frames, retain evenly spaced selection; for one, use the final
production frame. Retain original receptor components
and remove only solvent/ions added by the builder. Record ligand RMSD after
receptor-backbone alignment; do not invent a convergence claim or independence
of correlated frames. Prime uses verified native `-job_type ENERGY -frozen`
single-point calculations with explicit ligand identity and no minimization.
Store every frame score/time/RMSD, native journals, replica means, ensemble mean,
sample standard deviation (not SEM) and snapshot count. Publish only a complete,
finite ensemble across every requested replica.

SQLite stores `md_gbsa_score`, `md_gbsa_std`, `md_gbsa_snapshots`, immutable
`iteration_md_gbsa_selection` and `compound_md_gbsa_result`; preserve Prime scores.
The immediately preceding iteration's MD mean exclusively ranks elites and FEP
when that iteration has an MD policy (or MD is enabled). No Prime fallback for
missing MD results. Old frozen FEP selections are preserved; exporting them under
a newly added MD policy requires explicit reconciliation. Imports still honor the
original frozen FEP selection. Enough finite MD elites remain mandatory for TL,
which still starts from the original LibInvent prior.

Desmond runs through native `slurm-gpu`, one GPU per umbrella workflow, up to
`md-gbsa-gpus` (default/cap 20) active tasks in this project. Prime snapshot batches
run through `slurm-cpu` with at most `min(7, md-gbsa-cpus)` concurrent native jobs
(default CPU budget 384, existing seven-job ceiling retained). Ensembles queue
behind other active Prime work. A shared local submission mutex and durable SSH
registry extend Glide/Prime fencing across the local and Slurm JobServers.
Unknown launches and unavailable JobServers block resubmission/advancement.
Persist intent before submission, recover exact JobIds, require terminal native
parent/children and validated output before publishing; a zero launcher exit or
cached file alone never proves completion. Native checkpoints/output remain in
the frozen remote workspace; restart reconnects, never blindly restarts dynamics.
Confirmed native failure preserves checkpoints for operator-directed recovery.

Use `graft md-gbsa project.toml --iteration N` or the normal supervisor. Local
artifacts use `09-md_gbsa`; new FEP artifacts following it use `10-fep`. Preserve
existing `09-fep`/legacy directories and all saved job paths. Enabling MD for an
existing project backfills the latest completed generated iteration before the
next generation; it does not schedule MD for every historical iteration.


**Runtime opt-in repair (2026-10-21):** workers reload TOML independently. A
driver launched with MD disabled can otherwise reach FEP after MD is enabled,
then retry FEP forever because its original stage list omits MD. The supervisor
now rereads the MD flag at stage boundaries and before retrying blocked FEP,
inserts an independent MD worker after Prime/before FEP when needed, and waits
for persisted MD completion. Saved MD selections still recover when disabled;
no worker invokes another worker, no scores/jobs are reset, and frozen protocols
remain unchanged. A driver already running older code requires one graceful
restart; changing files cannot replace functions already loaded in that process.

**External evidence (isolated smoke, not production or 5 ns validation):**
on 2026-10-21, an optimized EGFR pair completed 160 ps standard relaxation,
2 ps extra equilibration and 2 ps production using GPU Desmond on Slurm `gpu56`,
Slurm job `99331`; the native log identifies `gdesmond`, GPU device 0 and CUDA.
Twenty distinct production snapshots passed atom/connectivity validation.
Prime snapshot scoring completed through `slurm-cpu` on `cpu2`, Slurm parent
`99332`, with 20 finite energies: mean −86.16107092256735 kcal/mol and sample
standard deviation 2.526891884724623 kcal/mol. Both native parents and all returned
children were verified terminal; both parents were DONE. The real ensemble was
stored in a separate validation SQLite DB and selected through the MD elite/FEP
APIs while preserving the copied Prime score (−83.11457050598983). Production DB
was opened read-only. Evidence: `examples/egfr-mol2mol/output/analysis/`
`md-gbsa-smoke-20261021T121655Z/validated-slurm-2026-1/verification.json`, with
snapshot TSV, terminal JobServer records and logs; native data remain in a
dedicated remote workspace. These numbers validate execution/collection, not
a converged affinity estimate.
The site JobServer is **2026-1, API v1.19.4**; installed 2026-3 client API v1.21.2
(and 2026-2 v1.20.5) cannot launch against it. Smoke execution explicitly used
`/share/opt/schrodinger/2026-1`. Default configured suite remains 2026-3; preflight
rejects this mismatch before claiming compounds. Set an explicitly compatible
`md-gbsa-schrodinger` or upgrade the site server; never silently downgrade or
restart a production JobServer. Full 5/20 ns accuracy/convergence remains unverified.

**Regression evidence:** 1,184 full-suite tests passed; an additional deleted-MD
recovery regression was subsequently included in a 74-test targeted pass.
Editable installation, syntax/import checks and diff whitespace checks passed.
An intermittent concurrent first-open WAL transition observed during testing was
fixed with bounded BUSY/LOCKED retries within the existing 30-second limit; other
SQLite errors propagate and close the connection.

Configuration example (remote paths remain remote):

```toml
md-gbsa = true
md-gbsa-production-ns = 5.0
md-gbsa-equilibration-ns = 1.0
md-gbsa-snapshots = 20
md-gbsa-analysis-start-fraction = 0.2
md-gbsa-input-ratio = 0.5
md-gbsa-input-count = 20  # overrides ratio
md-gbsa-replicates = 1
md-gbsa-seed = 20261021
md-gbsa-gpus = 20
md-gbsa-cpus = 384
md-gbsa-ssh-host = "slurm"
md-gbsa-gpu-host = "slurm-gpu"
md-gbsa-cpu-host = "slurm-cpu"
md-gbsa-schrodinger = "/share/opt/schrodinger/2026-1" # explicitly tested site version
md-gbsa-remote-work-dir = "/homes1/chchae/graft-md-gbsa-runs"
```

## Confirmed calculation naming (2026-10-21)

The user requested fresh projects and waived compatibility with prior work for
this rename. All Prime stage identifiers now use `prime_gbsa`, including
`prime_gbsa_score`, `prime_gbsa_running`, `prime_gbsa_done`, failure stage
`prime_gbsa`, CLI `graft prime_gbsa`, and directory `08-prime_gbsa`.
The user subsequently requested the same corrected spelling everywhere,
including Python module names, states, CLI, TOML, manifests and output paths.
TOML uses `prime-gbsa-input-count`, `prime-gbsa-flexdist`,
`prime-gbsa-license-retry-seconds`, and `prime-gbsa-license-retries`.
Other renamed Prime keys are `prime-gbsa-elite-count`, `prime-gbsa-receptor`,
and `prime-gbsa-cpus`. Internal Python settings retain `prime_gbsa_*`.
Native job basenames are `ligprep`, `glide`, and `prime_gbsa`; new Amber
Slurm names are `amber-<UUID>` without `graft-` or `run-`.
The external executable `prime_mmgbsa`, vendor Maestro property names such as
`r_psp_MMGBSA_dG_Bind`, and native JobServer recognition remain unchanged.
This is a naming change; selection, scientific parameters, seven-job Prime
limit, submission fencing, and completion/recovery checks remain in force.
No existing results, jobs, or project directories were deleted by this change.


## Two-attachment automatic MCS adjustment (2026-10-21)

**Two-attachment automatic MCS adjustment (2026-10-21):** the user delegated
selection of a safe policy for automatic MCS cores with more than two attachment
boundaries. For new automatic LibInvent generation snapshots in N>1, count
external bonds, including multiple bonds leaving the same core atom, before
choosing attachments. Do not silently select two sites and leave other branches
unrepresented. Validate exactly N-1's saved two-attachment scaffold against the
fixed docking core and all selected elites; missing/incompatible history stops
without saving a new snapshot.

Implementation choice under that delegation: preserve the previous retained
core and its two attachment labels. In the highest-ranked elite, consider
inferred-MCS embeddings containing that retained core and extend each attachment
along single-bond, acyclic, nonbranching common prefixes only. Stop before a
branch, ring, multiple bond, the inferred-MCS boundary, or a terminal atom that
would consume the entire decorator. Prefer the largest verified candidate,
using canonical scaffold SMILES to break ties. This is a conservative linear
extension rule, not a globally maximal two-boundary MCS search or a distance-only
site selection. It never deletes a dummy while leaving an undeclared branch.

Require exact canonical isomeric molecule recovery after decomposition and
reassembly for every selected elite, including the previous scaffold used as
fallback. Preserve the fixed docking core. If no extension passes, reuse the
validated preceding scaffold with `fallback_reason=excess_attachment_sites`.
Persist adjusted cores as `source=previous_attachment_guided_mcs`, with
`attachment_from_iteration`, `attempted_mcs_smarts`, `attempted_attachment_count`
and all current elite IDs. Fallbacks also record the attempted core/count.
This policy supersedes the earlier no-shrink rule only for excessive boundaries
in new automatic N>1 LibInvent cores. It does not change supplied cores,
iteration 1, already pinned fixed cores, saved snapshots, combichem, elite
selection, original-prior TL, vocabulary checks or completion gates. New fixed
mode may pin the normally adjusted first choice; later resumes reuse it.
Validation covers molecular graph decomposition/reassembly, not new target
ranking, real TL/sampling or docking/GBSA calculations.

## Symmetric single-elite scaffold matching (2026-10-20)

Iteration 7 rejected elite 62121 because the first symmetric atom embedding put
an attachment dummy on a retained methyl branch. The saved scaffold and fixed
docking core are compatible; a later embedding decomposes and reassembles exactly.
Validate alternative non-unique RDKit matches using the existing branch/component
rules, retain the first valid decomposition, and reuse its exact atom map for
single-elite replacement cuts. Do not relax core/attachment constraints or change
saved snapshots, elite ranking, scores or submissions. Regression tests cover
charged/neutral branch symmetry, reassembly, cut membership and invalid branches/
bridged decorators. Read-only RDKit reproduction used the actual iteration-6
elite and saved scaffold; no new generation, docking or GBSA was launched.

## Amber SLURM 30-minute limit (2026-10-20)

**Superseding user change (2026-10-21):** newly created Amber Slurm array
records now pin `slurm_time_limit="00:10:00"` and submit with
`sbatch --time=00:10:00`, limiting each original-compound task to 10 minutes of
allocation runtime including preparation/calculation and excluding queue waiting.
Existing records keep their saved 30-minute or partition-default limits, including
not-yet-prepared records. Do not update existing jobs or uploaded inputs/workers.
The verified TIMEOUT recovery policy below remains unchanged: retain scores,
record unscored timed-out compounds as terminal Amber failures, and proceed through
the existing readiness/ranking gates without resubmission. Direct SSH is unchanged.
The original 30-minute policy below is retained as historical context only.
TOML now exposes `amber-slurm-time-limit = 600` (default), in seconds.
Validate a positive integer (reject bool, float and strings), normalize to
`HH:MM:SS` for Slurm, and pin it only when creating new array records.
Keep it outside remote-environment settings so editing the TOML does not block
recovery of an existing manifest, which retains its original time limit.

New array manifests pin `slurm_time_limit="00:30:00"`; propagate it to remote
input and pass `--time=00:30:00` to sbatch. The limit is per compound task,
includes receptor setup and excludes queue waiting. Saved records lacking the
field keep their prior limit even if not yet prepared. Leave uploaded workers,
existing jobs, legacy recovery and direct SSH work intact.

Later user-approved correction: terminal one-compound array TIMEOUTs become
per-compound Amber failures when they have no result, allowing Prime to proceed
through existing gates. Require complete task/step history and all locks released;
other tasks still need native success, done markers and results. Preserve existing
scores, Prime/FEP states and scientific workers. Use saved input entry order for
mapping, archive timeout IDs/native state and download logs before retirement.
Current read-only helpers also reconcile saved arrays; do not resubmit them.
Other failures/uncertainty retain work. If squeue returns "Invalid job id
specified" for an evicted finished job, query sacct and require complete verified
history. Other squeue errors remain uncertain. See SPEC section 22. Regression
tests cover queue eviction; read-only inspection of job 87945 confirmed six
TIMEOUT tasks and subsequent archived recovery with Prime claims. No new
scientific calculation was launched for verification.

## Amber one-core Slurm arrays (2026-10-20)

New Slurm records use `slurm_array_v1`, one original compound per 1-core array
task, with concurrency capped by `min(compound count, amber-slurm-cpus)` (1..384).
Request 2 GiB per task instead of all node memory. This initial memory choice
requires real workload validation. Stage supervision, direct t41 execution and
scientific parameters are unchanged. See SPEC section 21.

`amber_array.py` is bundled beside the standalone worker. Locally prepare
`tasks/<index>/input.json` with one entry and cpus=1 plus relative read-only input
links. Receptor preparation repeats in each task's private directory; all mutable
products, inherited locks, logs, markers and results remain private. The stage
collects partial results and validates all expected task IDs/steps plus all
markers/lock releases before retiring the saved array. Recover lost sbatch replies
using array base IDs from squeue and task IDs from sacct. Missing/ambiguous history
never authorizes resubmission. Retain failed/uncertain manifests and scores.

Keep saved `slurm_batches_v1`/`slurm_single_v1` allocations on their exact legacy
recovery path, including existing uploaded workers/resources. Never migrate live
work. Download per-task receptor logs as `CMPID_<id>/receptor_calculation.log`.
Mock scheduling/recovery tests do not validate real array calculations or speed.

## Generation element filter (2026-10-20)

New combichem/LibInvent batch admissions allow only H, C, N, O, F, P, S, Cl, Br
and I, using RDKit atomic numbers in `read_generated_smiles`. Reject any other
element or dummy atom before descriptors/selection/target insertion, for both
raw and legacy policies, regardless of Amber's enabled flag. This is a project
GAFF2/AM1-BCC allowlist, not the full standalone AM1 parameter set. Log rejected
row counts and per-element counts, keeping source CSVs. Existing rows, scores,
reference imports, saved plans/cursors and submitted work remain unchanged.
No calculation parameters change; element eligibility does not assure convergence.

## Prime sidechain-only flexibility (2026-10-20)

SPEC section 20 supersedes the historical whole-residue choice for new native
Prime batches. Positive `prime-gbsa-flexdist` now uses `-rflexgroup side`, preserving
the configured distance (EGFR: 6 Å), flexible ligand, per-ligand complex inputs
and optimized complex output. Existing native manifests and legacy queues keep
their original commands/protocols on restart and license retry. No scientific
results or live jobs are changed. Installed 2026-3 help verifies the option;
mock regression tests do not validate actual speed, energy or ranking.

## Deleted-iteration restart (2026-10-20)

SPEC section 18 extends clean-start cleanup: zero compounds plus durable evidence
of a prior population triggers automatic archive and metadata reset before new
generation. Preserve zero-row preparation retries and all nonempty populations.
Use `_workspace.py` for file/job/lock checks and `database.py` for transactional
reset plus a full SQLite/WAL backup. Retain all original files and other iterations;
never reset live/uncertain work. Druglike membership/cursor snapshots must be reset
together, using attributable original IDs only. Each new druglike selection row
now stores its iteration atomically, so deleting both compounds and output files
still leaves membership evidence. Migration backfills only surviving compounds;
legacy orphan rows remain unattributed. Reset accepts stored selection ownership,
origins or the original report and rejects conflicting sources. Missing legacy
ownership still blocks automatic cleanup; do not infer ID ranges or remove global
orphans. Use the actual `synthetic_feasibility` stage lock alongside other locks.
Corrupt partially deleted frozen populations fail before expensive sampling. The already running EGFR driver
successfully recovered iteration 2 with this path; backup is in `.previous`.

## Native Prime batching (2026-10-20)

SPEC section 17 supersedes individual buffering for new Prime calculations.
Use `prime_gbsa_batch.py` and `native_batch_v1`: one request for all selected unscored
inputs, one ligand per native subjob, aggregate seven HOST slots. JobDJ owns
scheduling. Keep legacy `prime_gbsa_queue.py` recovery for submitted individual queues.
Retain immediate subjob DB imports, JobId recovery, cross-project Glide exclusion,
and project-wide admission across iterations. Flexible batches use independent
complex inputs to avoid native PV union-of-receptor-regions behavior. Keep
partial scores/coordinates and failed manifest audits. No production jobs are
migrated or launched by this code change; numerical equivalence requires an
isolated real scientific validation.

## Latest staged selection policy (2026-10-20)

SPEC section 16 is the current authority for new iterations. It supersedes the
historical pre-DB selection and Amber-200/Prime-50 descriptions retained below
for legacy recovery. New flow: Generate(DB raw target) → Drug likeness over the
whole iteration → optional synthetic → LigPrep → Glide → Amber best 500 → Prime
best 200 → FEP candidate export best 20. All workers remain independent.

New workspace rows pin generation_policy=raw_v1; old rows default to legacy.
Raw generation stores valid unique compounds before computing properties.
The druglike worker calculates all properties, applies global RO5/QED/SA
selection and freezes passing IDs/reasons in SQLite. Rejected rows remain
`druglike_rejected`. Default optional SA ratio is 1.0; explicit smaller values
apply only within this druglike stage. Legacy iterations keep original targets,
selection, compounds, state and submitted jobs. Reject raw inserts after a frozen
selection. CLI exposes `graft druglike project.toml --iteration N`.

Canonical TOML count is `amber-input-count` (default 500), which limits the
Glide-ranked Amber inputs. Legacy `amber-keep-count` remains an alias; reject
invalid values or unequal simultaneous aliases. Keep internal worker/API names
and saved submission settings unchanged for recovery compatibility.

Prime uses finite Amber ranking, falling back to Glide only when no Amber score
exists after required work completes. Partial sets are never mixed or filled.
FEP similarly uses one finite score tier: Prime, else Amber, else Glide; freeze
IDs/source/ranking scores for restart and import. Counts are maxima, not promises
of successful results. Active, uncertain, retryable or saved work blocks fallback.
Keep 7 Prime slots and native recovery. FEP remains export/import only; no
production FEP protocol is introduced. Next generation still requires Prime
elites, independently of FEP candidate fallback.

The EGFR TOML enables Amber 500, Prime 200 and FEP export 20 with raw target
10,000 and no additional SA fraction cut. Existing reference/host/program
settings are retained. No live calculations are authorized by editing this file.

## 1. Project Goal

Graft is an iterative lead-optimization platform integrating:

REINVENT4 / LibInvent
→ LigPrep
→ reference-guided constrained Glide
→ MM-GBSA
→ optional FEP+
→ elite selection
→ next LibInvent iteration

The primary application is congeneric lead optimization around a known scaffold/core.

## 2. Package Architecture

Graft uses a standard Python `src/` layout:

graft/
├── pyproject.toml
├── AGENTS.md
├── README.md
├── docs/
├── examples/
├── src/graft/
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   ├── states.py
│   ├── driver.py
│   ├── _core.py
│   ├── stages/
│   │   ├── generate.py
│   │   ├── ligprep.py
│   │   ├── glide.py
│   │   ├── prime_gbsa.py
│   │   └── fep.py
│   └── chemistry/
└── tests/

The large `_core.py` currently preserves validated computational
logic from the original working prototype.

Do not rewrite it wholesale.

Move functionality out of `_core.py` incrementally as modules become
stable and adequately tested.

## 3. CLI

Development installation:

    python -m pip install -e .

Main commands:

    graft run project.toml
    graft generate project.toml
    graft ligprep project.toml
    graft glide project.toml
    graft prime_gbsa project.toml
    graft fep project.toml
    graft status project.toml

`graft run` starts the supervisor.

Individual stage commands are useful for debugging and manual recovery.

## 4. Project Isolation

Graft source is installed once.

Each molecular target has a separate working directory:

projects/
├── egfr/
│   ├── egfr.toml
│   ├── input/
│   └── output/
│       └── egfr.sqlite
├── cdk2/
└── ask1/

Relative paths in TOML are resolved relative to the TOML file.
The project name defaults to the TOML filename stem. SQLite is stored as
`<out-dir>/<project>.sqlite`. When this output database is absent, the old database
beside the TOML is copied using SQLite backup; the original is retained.

## 5. Workflow Architecture

SQLite is the workflow source of truth.

The driver uses one thread to supervise independent stage subprocesses in order:

generate → synthetic (if enabled) → ligprep → glide → amber-gbsa (if enabled) → prime_gbsa → fep export (if enabled)

Only one stage worker subprocess runs at a time. The driver selects an iteration
once (explicit `--iteration N`, otherwise oldest unfinished or next new) and
passes it to every worker. It exits after that iteration, without advancing N+1.
Workers retain DB inspection, atomic claims, calculation and persistence. Workers
do not invoke one another and scientific calculation code stays outside the driver.

Glide now shares `glide-cpus` (or explicit host slots) across reference groups
in proportion to prepared ligand structure counts. Group `HOST`/`NJOBS` requests
fit that shared budget and are capped by group structure count. Reserve saved
live/uncertain requests first; preserve legacy jobs unchanged and defer new
groups when slots are exhausted. Bare hosts retain vendor defaults.
See SPEC section 8 for allocation, restart and count-metadata behavior.

A zero subprocess exit is not completion. Check SQLite state and native JobServer
parent/child completion before advancing; retry only the current incomplete stage.
Preserve durable submission recovery and the cross-project submission mutex.
Glide groups still execute in parallel and persist each completed group; MM-GBSA
retains seven slots, individual refill and immediate score persistence. At log level 1, normal queue polls
show a single line: iteration-wide running claims and saved finite scores, queue
active/waiting/limit, and newly updated scores. Move repeated recovering/submitted
messages and routine driver still-running pending messages to level 2. Preserve
work-start protocol/timers, completion gates and all diagnostic/error output.
 Poll
MM-GBSA at most every five seconds. `--once` stops at the first incomplete/failed
stage; later stages run only after verified completion. FEP remains export/import.

Generation validates/canonicalizes/deduplicates the full sampled batch first,
then computes all 16 `rdk_` properties before DB target-count selection.
Druglikeness preselection keeps RO5<=1 and at most ceil(valid novel count/2),
ranked by QED descending then SA ascending (stable input ties). Return the
annotated selection in input order; insert passing rows in that order up to the
remaining iteration target. Recheck duplicates and target in one transaction;
insert descriptors with new compounds instead of recalculating after insertion.
Save per-batch `druglike_selection-<timestamp>.csv` (including rejected rows,
properties and reasons) beside the sampling CSV. Batch indices are not DB IDs.
A property failure leaves sampling inputs and existing DB contents intact.
Underfilled iterations sample again; zero accepted insertions raise an error.
This remains independent of AiZynthFinder. See SPEC section 6 for field
names/types and exact descriptor/alert definitions. `rdk_sa_score` is canonical;
keep legacy `sa_score` as a compatibility alias and migrate/backfill missing
values without changing existing results, states or failure diagnostics. The
same local CPU limit handles the full property batch; calculations finish before
an atomic DB update. Completed generation reruns backfill missing properties,
including rejected/processed compounds. Initial reference import also calculates
all 16 properties. Opening an existing project fills missing iteration-0
properties from DB SMILES without rereading MAEGZ; use `sa-score-cpus` and commit
only after the complete batch calculates successfully. Keep existing values,
scientific scores, states and failure diagnostics. References undergo neither
druglikeness selection nor SA acceptance filtering; retain their entire population.
Only RO5/QED/SA enter druglikeness selection; Veber/alerts are report-only. The
existing subsequent SA acceptance filter remains separately configured. `sa-score-cpus` defaults to 1 and enables
local process parallelism when increased. An explicit generation rerun backfills
missing scores even at target count; scores/states/results already present are
preserved, and scoring failures retain sampling inputs/existing compounds for retry.
After the full iteration is scored, `sa-accepted-ratio` (default 0.75, range
`0 < ratio <= 1`) keeps `ceil(count * ratio)` compounds ordered by SA score/ID.
Persist the selection in `iteration_sa_selection` and mark excluded rows
`sa_rejected` atomically; preserve their scores and exclude them from all later
stages and synthetic readiness checks. Target count remains the total generated
population admitted after druglikeness preselection. Reuse the saved selection on restart/config changes. Never apply
a new filter to iteration 0 or legacy iterations with downstream work/results.

Before first generation of a new SQLite iteration, after completion/elite gates,
clear any leftover `iterN` tree into `output/.previous/iterN-<timestamp>/` and start
with an empty directory. Check native jobs and saved/logged parent/child JobIds
before moving anything; live/unknown/uncertain or unresolved remote submissions
block cleanup. Protect configured inputs and reject root symlinks. Preserve DB
results, all saved-work iterations and iteration 0. Checkpoint preparation in the
non-destructively added `iteration_workspace` table before generating, so zero-row
retries retain their files even without a saved scaffold. Timing alone does not
count as saved work. Both combichem and REINVENT use this path under the generation
stage lock. Archiving isolates old results without reclaiming disk space.

New LigPrep/Glide/Prime calculations clear stale working artifacts from the
active path before rebuilding inputs. Verified terminal historical work is
archived; same-submission restarts recover JobIds, and live/uncertain jobs block
cleanup. Zero mapped LigPrep IDs cannot complete Glide. The specific historical
missing-LigPrep-input failure can be retried through LigPrep without resetting
other failures or scores. Entirely obsolete Prime queues are archived only after
all saved child jobs are verified terminal/unsubmitted. Interrupted obsolete
children use the existing launch-client/log/JobServer reconciliation to recover
missing JobIds or establish that no submission occurred, without submitting old
compound IDs. Query failures retain the queue and report the cause. See SPEC section 8.

## 6. SQLite

Use:

    PRAGMA journal_mode=WAL;
    PRAGMA busy_timeout=30000;
    PRAGMA foreign_keys=ON;

One row represents one original compound.

Core fields:

    id
    name
    smiles
    iteration
    dG_exp
    docking_score
    prime_gbsa_score
    fep_score
    similar_to
    similarity
    state
    failed_stage
    failure_message
    created_at
    modified_at

`name` must be unique.

`similar_to` refers to an iteration-0 compound.

Database migrations must preserve existing results.

## 7. State Model

Stable workflow:

    reference
    generated
    ligprep_running
    ligprepped
    glide_running
    docked
    prime_gbsa_running
    prime_gbsa_done
    fep_running
    fep_done
    failed

Use one state column rather than one status column per stage.

Transient failures may return the compound to the last stable state.

Permanent failures use:

    state = failed
    failed_stage = <stage>
    failure_message = <diagnostic>

Atomic claiming should be implemented using SQLite transactions.

## 8. Reference Compounds

Iteration 0 contains known reference compounds and poses.

Reference structures are imported from a multi-structure MAEGZ.

Structure title is used as the reference compound name.

Iteration 0 is never generated by REINVENT.

## 9. MCS and Scaffold

Three cases are supported.

### No user MCS

`reference-mcs` defines the fixed iteration-0 docking constraint only, except
for the explicit first-generation fallback below. A docking SMARTS does not need
LibInvent attachments. Persist row 0 and never change it for generation input.

`elite-mcs-fixed` is a strict TOML boolean, default false. True pins the first
chosen generation core **and scaffold attachment positions**, then reuses them
across new cycles/restarts. Record `elite_mcs_fixed` and `fixed_from_iteration`
in immutable generation snapshots; prefer the first previous fixed snapshot,
or the original saved scaffold for the same supplied value when enabling the
mode later. Fixed mode can use the saved initial fallback when elite input is
absent. False uses the adaptive policy below. Flag changes affect only new
snapshots; disabling fixed mode resumes elite-derived MCS, and re-enabling it
reuses the prior pin. TL still uses immediately preceding elites and original
prior; all gates, scientific compatibility and docking constraints remain.

Optional `elite-mcs` supplies the first new generation core once, including
when starting at iteration 3. Iteration 4 and later infer their generation cores
from exactly the selected finite Prime elites of the preceding iteration
(`prime-gbsa-elite-count`, 20 in EGFR), after completion gates. If `elite-mcs` is absent,
store `reference-mcs` as `initial_elite_mcs` in the iteration-0 bootstrap snapshot
and use it once for the project's first generation. Earlier generated rows or
scaffold snapshots, including legacy ones, prevent fallback reapplication to
a continuing project. Row 0 does not consume that application.
Preserve the saved fallback on restart; explicit `elite-mcs` takes precedence. If both
are absent, infer the first generation core from iteration-0 references.
Persist `supplied_elite_mcs` in the immutable generation snapshot; resumes and
failed TL/sampling reuse it. A new supplied value applies once at the next new
snapshot. Existing snapshots/jobs/results remain intact. Use preceding elites
for later-generation attachment analysis and TL from the original prior.

The generation scaffold must contain the fixed docking core and support elite
decomposition/LibInvent attachments. Publish separate generation/docking files
in `iterN/scaffold/`. Existing legacy cores remain immutable; missing original
files stop recovery rather than changing live calculations. Logs label recorded user elite cores as `saved elite-mcs`, explicitly noting
when the current TOML omits or differs from that input; preserve the snapshot
when configuration changes. Generation logs display the stored scaffold SMILES
beside the generation SMARTS, including resumed/completed generation. Glide logs
only docking constraints; suppress generation core/SMILES in that stage.
Generation may display a relaxed SMARTS summary with generic ring annotations
omitted; label it `generation core summary`. Full queries remain in SQLite and
`mcs.smarts`; preserve scientific MCS/matching conditions, stereochemistry and
specific ring-count queries. Reporting never recomputes cores or changes scientific results.
Iteration 0 has no generation stage. See SPEC section 6 for the current input policy.

### User MCS without attachment points

Use the supplied core and determine attachment positions.

### User LibInvent scaffold

If the supplied expression already contains `[*:0]` and `[*:1]`,
treat it as the complete LibInvent scaffold.

Do not add dummy atoms again.

References that do not match the MCS should be excluded from the
pose-template candidate set rather than aborting the project.

## 10. REINVENT4 / LibInvent

Optional `sidechain-max-heavy-atoms` is a positive integer limiting each incoming
library fragment before sampling (inclusive; hydrogen/isotopes and dummy excluded).
Omission is unlimited. Report oversize fragments in `sidechain_rejected.tsv` and
fail if none remain. Save the limit in each new iteration plan; refills and
attachment retries use the saved value, regardless of current TOML. Legacy plans
remain unlimited. Existing pools, compounds, results and cursors are preserved.

Optional local `sidechain-library` selects the combichem generator in the same
generate worker; absence retains REINVENT4. Combichem has no prior/runtime/TL
requirement. Use one or more explicit scaffold dummy positions, or every inferred
boundary, and only single-bond assembly. Choose up to 1000 unique sidechains per
site with weight 1/heavy-atoms; fragments need exactly one terminal dummy attached
to a heavy atom by a single bond. Carbon-free fragments are allowed. Report invalid/duplicate rows. Traverse the full Cartesian
candidate space lazily, preserving existing descriptor/druglikeness/SA filters
and target-count. The user confirmed stopping at target-count; do not assemble
unvisited combinations after reaching it. Persist pools, library identity, seed, ordering, batch size and
cursor in SQLite; insertion and cursor advancement share a transaction. New
iterations redraw pools; restart retains the committed active round. When a
sampled pool is exhausted below target, append independently drawn pools and
continue generation, preserving every compound and scientific result. Archive
the previous immutable plan/cursor in `iteration_combichem_history` and install
the next numbered plan atomically; progress checks include the round identity.
Legacy plans are round 0; new rounds use separate artifact subdirectories.
Require the pinned library path/SHA256 for refills, while an active saved round
can resume without that file. If each pool covers the full eligible library,
report incomplete exhaustion instead of repeatedly redrawing the same full space.
Adaptive single-elite replacement scaffolds also retry untried attachment cut
combinations after 10 consecutive committed batches insert zero new compounds.
Count across pool refills/restarts; one insertion resets the streak. Keep the
base snapshot immutable and archive partial rounds before installing the next
variant with fresh pools. Persist batch outcomes and compound-to-round origins
atomically with insertion/cursor advancement; use each elite's actual producing
round scaffold for later single-elite cycles. Old records remain intact and old
batches do not acquire inferred failure counts. Fixed/supplied cores and
multi-elite MCS retain their policy. See SPEC section 6 for details.
See SPEC section 6 for transaction/recovery details. Preserve original core
policy, preceding-elite gates, fixed docking,
downstream workers, scientific results and existing jobs. Saved unfinished
iterations cannot change generator. See SPEC section 6 for the confirmed policy
and implementation choices. REINVENT4 behavior below remains unchanged.

For new automatic generation cores in N>1, fewer than two attachment boundaries
triggers validation of exactly N-1's saved generation scaffold. Reuse only if
its core/scaffold is valid, it has exactly two terminal single-bond dummy
attachments labelled 0/1, contains the saved generation and fixed docking cores,
and decomposes every selected elite. Otherwise stop; do not try older cores,
shrink the MCS or replace elites. Supplied cores, initial generation and other
MCS/scaffold errors do not use this fallback. Persist the failed automatic query,
fallback source iteration/reason and current elite IDs in the new immutable
snapshot; resumes restore it. Keep prior snapshots, scientific results/jobs,
completion gates, fixed pinning, original-prior TL and vocabulary preflight intact.

Iteration 1:

    original libinvent.prior
    + fixed scaffold
    → sampling

Later iterations:

    selected previous compounds
    → short transfer learning from ORIGINAL prior
    → sampling

Do not continue training the previous TL model.

Typical defaults:

    sample-size = 3000
    target-count = 1000
    max-iteration = 20
    elite-tl-epochs = 3

Generated molecules must be:

- RDKit valid
- canonicalized
- unique within the generation
- absent from previous database generations

### LibInvent dummy notation

Scaffold vocabulary:

    [*]

Decoration vocabulary:

    *

These are intentionally different.

## 11. LigPrep

One LigPrep job is normally used per iteration.

Input structure title:

    CMPID_<database id>

Current settings:

    pH = 7.4
    tolerance = 1.0
    Epik max states = 2
    max stereo = 1

Conceptual command:

    ligprep \
      -ismi input.smi \
      -omae ligprep.maegz \
      -epik \
      -We,-ph,7.4,-pht,1.0,-ms,2 \
      -s 1 \
      -HOST <host> \
      -WAIT

Do not use:

    -r 1

with the current Schrödinger 2026-3 environment.

## 12. Reference-Guided Glide

For every generated compound:

1. consider MCS-compatible iteration-0 references,
2. calculate Morgan fingerprint radius 2, fpSize 2048,
3. calculate Tanimoto similarities,
4. choose the most similar reference,
5. store its compound ID in `similar_to`,
6. store Tanimoto in `similarity`.

The MCS defines the constrained core.

The nearest reference determines the reference 3D pose.

Prepared compounds are grouped by `similar_to`.

Each group is submitted independently to Glide/JobServer/SLURM.

Use constrained SP docking.

Do not silently fall back to unconstrained docking.

## 13. Compound-Level Docking Result

LigPrep may produce several states from one original compound.

All retain the same identity:

    CMPID_<database id>

After Glide, group structures by this ID.

For each original compound select the pose with the minimum:

    r_i_docking_score

Store it as:

    docking_score

Store one representative best pose per compound in:

    iterN/glide/best_poses.maegz

## 14. Streaming Glide Results

Do not wait for every reference-group Glide job before updating SQLite.

Required behavior:

    group A finishes
    → parse immediately
    → update docking_score/state

    group B finishes
    → update immediately

    group C remains running
    → A/B results remain persisted; MM-GBSA waits for C

Default:

    completion_fraction = 0.95
    tail_timeout = 300

`tail_timeout` must be configurable in TOML.

Do not forcibly terminate external jobs merely because the local
worker reaches its tail timeout. The separate per-group `glide-job-timeout`
(default 1800 seconds, 0 disables) authorizes native parent/child stopping.
Persist timeout intent before `jsc stop --force`; retain running claims until
all jobs terminate, then mark unscored group compounds failed. Native start time
(submission time fallback) survives worker restarts.

A later Glide worker invocation should recover completed outputs.

## 15. MM-GBSA

Optional TOML `prime-gbsa-flexdist = 6.0` enables whole-residue receptor flexibility
within 6 Å of each ligand. Default/zero keeps the frozen receptor and flexible
ligand. Validate finite nonnegative numeric values; use installed-2026-3-verified
`-rflexdist <distance> -rflexgroup residue` only for positive values. In that
mode use `-out_type COMPLEX` to retain optimized receptor coordinates. Pin the
distance in new queue manifests, including for pending members, reference work,
restart and license retries. Missing legacy metadata means zero. Saved child
commands and legacy batch commands remain exact; do not reset jobs or scores.
Preserve the seven-job limit, gates and submission fence. Native parser and
ASL selection were verified without Prime submission; no minimization/energy,
speed or ranking validation is claimed for this new mode.

Prime MM-GBSA works on the final top-N after the entire iteration finishes docking
and enabled Amber screening. Rank by Amber score when enabled, docking otherwise.

Typical configuration:

    prime-gbsa-input-count = 50
    prime-gbsa-elite-count = 10

The worker should:

1. wait for `target-count` generated compounds and no upstream states
   (`generated`, `synthetic_running`, `ligprep_running`, `ligprepped`, `glide_running`),
2. rank final Amber scores when enabled (docking otherwise), using stable ties,
3. take the first `prime-gbsa-input-count` compounds, including already scored compounds,
4. atomically claim only eligible candidates without `prime_gbsa_score`,
5. calculate those and persist `prime_gbsa_score`.

Readiness, ranking and claiming occur in one SQLite write transaction.
Terminal upstream failures count as processed. Retryable failures, unfinished
generation and Glide tail timeouts block MM-GBSA. Explicit iteration requests
obey this gate. Preserve old scores and submitted jobs from the former streaming
policy; recover saved jobs after docking completes before submitting missing
final top-N compounds.

Lower GBSA score is considered better for ranking.

## 16. Next Iteration Eligibility

Do not generate N+1 until generation and LigPrep/Glide in iteration N
have finished, no compound remains `prime_gbsa_running`, and every compound in
the final selected top-N has a Prime GBSA score or a terminal failure. Retryable
failures and missing scores must finish before advancing. Docked compounds
outside the top-N do not require MM-GBSA.

At least `prime-gbsa-elite-count` finite GBSA results are required. Select the
lowest GBSA scores from iteration N only to drive the next LibInvent TL
cycle; do not pool elites from older iterations. Terminal failures supply
no elite. MM-GBSA starts only after Glide finishes within its iteration.

Explicit generation requests obey the same gate. When resuming a database
created under the former early-generation policy, all earlier iterations
must also satisfy the completion gate. Existing compounds are retained.

## 17. FEP

The database contains:

    fep_score

FEP is optional and disabled by default.

Until the production FEP+ protocol is defined, the FEP stage should
support:

- candidate selection/export
- external result import

Do not invent a production FEP+ workflow without explicit protocol
requirements.

## 18. Configuration Direction

Preferred long-term TOML organization:

    [project]
    [reinvent]
    [structure]
    [ligprep]
    [glide]
    [prime_gbsa]
    [fep]

Backward compatibility with existing flat TOML files should be
preserved while migrating.

## 19. Runtime Environment

Development may occur on macOS.

Production calculations run on Linux with:

- REINVENT4 conda environment
- Schrödinger Suite 2026-3
- JobServer / observer
- SLURM
- CPU compute hosts

REINVENT4 and Schrödinger are external runtime dependencies and
should not be added as normal pip dependencies.

## 20. Development Priorities

Priority order:

1. atomic SQLite work claiming
2. reliable crash/restart recovery
3. idempotent stage workers
4. centralize SQL in database.py
5. centralize config validation in config.py
6. logging cleanup
7. incremental `_core.py` decomposition
8. automated tests

Important tests:

- schema migration
- state transitions
- atomic claiming
- duplicate prevention
- restart behavior
- nearest-reference selection
- LibInvent dummy conversion
- docking top-N selection
- MM-GBSA top-N updates

## 21. Rules for Refactoring

Before changing code:

1. inspect the current implementation,
2. compare it against this document,
3. identify the smallest necessary change.

Do not rewrite working scientific code merely for stylistic reasons.

After changing code:

1. run relevant tests,
2. run syntax/import checks,
3. summarize changed files,
4. report behavioral changes,
5. identify unresolved risks.

The primary design principle remains:

    Workers do not coordinate directly.
    SQLite coordinates the workflow.

## Restart recovery update

New MM-GBSA claims accept only `docked` compounds. Under the MM-GBSA stage
lock, reset unscored `prime_gbsa_running` claims with no saved submission to `docked`;
protect every ID in the saved Prime record. Recover the exact saved job before
submitting missing final top-N candidates. Do not reset active external jobs.
LigPrep records submission identity in `ligprep_job.json` before launch and
saves JobId while reading the existing `-WAIT` command output. On restart,
query the parent job and download only terminal output. Retain submission
records until compound states have committed; unknown launches/status failures
must not trigger resubmission. Legacy LigPrep logs require a recoverable JobId.

## Streaming GBSA results and elapsed times

While a saved Prime parent runs, query its direct children and import only DONE
ligand subjobs with matching native names. Read each immutable MAEGZ snapshot
using `jsc tail-file --name <subjob>-out.maegz --force <parent-or-child-JobId>`;
this also works after the parent has collected the child's output. Do not use
`jsc download` for a running parent, nor consume a partially written output.
Keep cache paths isolated by parent/child JobId. Use the existing score extractor
and validate all returned compound IDs against the saved submission. Commit scores
before checkpointing imported child IDs. Mark scored compounds `prime_gbsa_done` and
keep unfinished compounds `prime_gbsa_running`. A later parent failure must reset only
unscored claims; preserve streamed results and later states. Final parent output
import remains idempotent. Partial results never bypass the iteration gates.

Report active stage invocation elapsed time (including failures) separately from
external calculation duration. GBSA uses JobServer start/end timestamps; LigPrep
retains submission timing for restart, and Glide group timing comes from native
completed logs. Do not clutter successful idle polls with timing messages.


## Per-compound MM-GBSA queue

New submissions use `prime_gbsa_queue.py` and manifest mode `compound_queue_v1`.
Claim the final top-N once, split the validated PV without changing titles,
properties or scientific parameters, and submit receptor + one ligand with
`-NJOBS 1`. Limit active/uncertain submissions to seven across project iterations.
Use per-compound Prime records for license retry and JobId recovery. Persist
scores before queue progress, and refill each finished slot independently.
MM-GBSA supervisor interval is capped at five seconds; workers remain independent
subprocesses and exit after each queue pass. Preserve and recover legacy native
batches; their recorded resource requests block new queue submissions in other
iterations until retired. Never cancel or reset their live jobs during migration.
Single-compound execution optimizes the free receptor for each job using the
same Prime settings; unlike a batch, this repeats receptor preparation work.

When the entire queue finishes, print MM-GBSA total wall time in HH:MM:SS.s,
including waiting, collection and restart downtime. Persist `started_at` and
`finished_at` in the queue manifest and completed archive. For older queue
manifests, recover the start from the nanosecond timestamp in `queue-<timestamp>`.
This total is separate from each native job's calculation duration.


## Glide / MM-GBSA submission exclusion and durable Glide identity

Do not overlap Glide and MM-GBSA native jobs. Use `schrodinger_guard.py` for
active JobServer parent/child checks and an atomic shared submission mutex on
the launch host, including legacy Prime retries. Pause GBSA refill while any
Glide lives; collect already submitted results normally. Prevent new Glide
submission while Prime lives. Keep workers independent; driver supervision and
streaming persistence remain unchanged.

Glide records submission identity before launch in each group's `glide_job.json`.
Recover legacy live jobs by launch directory, taking precedence over old logs and
outputs from earlier attempts. Parent and children must terminate before result
import; download that exact completed submission. A failed child log line alone
must never cause duplicate parent submission. Unknown launches/status queries
retain work. Never remove live job files when a LigPrep fingerprint changes.


## Interrupted Prime launch reconciliation

Checkpoint JobId immediately on each launch-client output line, before waiting
for the client to exit. Store launch host and start time before spawning. For a
record with launching=true and no JobId, check surviving submission clients and
use verified `jsc list --any-status --launch-dir <directory> --id-only` plus
`jsc info --json` to recover running or terminal native jobs. Recover matching
new log JobIds as well. Never infer absence from an empty Slurm queue alone.
Only release a launch for guarded retry when JobServer history lookup succeeds,
no client or unidentified calculation artifact exists, and a 60-second launch
registration grace period has passed. Preserve an audit copy and scientific
scores. Unknown hosts, lookup failures and ambiguous histories remain pending.


## Iteration elapsed time

The driver checkpoints iteration start and verified completion in SQLite
`iteration_timing`. Print total wall time in HH:MM:SS.s after all configured
stages have completed, including polling, recovery and downtime between
restarts. Never checkpoint completion on a pause or failed/incomplete stage.
For existing iterations without a timer, use their earliest compound creation
UTC timestamp and label the duration as estimated. Preserve the first finish
on reruns, and preserve scientific results.


## Reference MM-GBSA

Iteration 0 is eligible for MM-GBSA when references lack `prime_gbsa_score`. Use all
unscored reference poses, independent of docking scores, target-count and top-N.
Claim `reference` rows atomically, recover saved iter0 queues, and restore orphan
claims to `reference`. Use the same receptor source and per-compound queue as
later iterations. Map original reference titles to SQLite IDs when building the
PV and rename only calculation inputs to CMPID_<id>. Existing scores, reference
names, coordinates, experimental values and reference selection remain intact.
New DB import preserves finite GBSA scores present in reference input properties.
Default driver selects iter0 first; an explicit later iteration completes pending
reference work first. Iteration 0 runs Amber GBSA (when enabled) before the MM-GBSA worker, without generation,
LigPrep, Glide or FEP. Terminal failures remain terminal and provide no score.


## LibInvent TL vocabulary preflight

Before writing training/validation pairs, load the configured original prior's
vocabulary and REINVENT token pattern on CPU in its local or SSH runtime.
Check actual scaffold/decoration vocabularies and the installed reader gate.
Skip unsupported-token elite pairs before splitting; persist IDs and reasons
in `elite_skipped.tsv` and vocabulary metadata in `elite_vocabulary.json`.
Preserve original molecular strings, database compounds, and scientific scores.
Never bypass a failed vocabulary lookup, replace unsupported tokens, or extend
the prior vocabulary implicitly. At least two compatible pairs remain required.


## Final MM-GBSA coordinates

Confirmed addition requested on 2026-10-12: after MM-GBSA completes, publish
`iterN/prime_gbsa/prime_gbsa_final.maegz` containing the native final structures from
successful results. Keep receptor and ligand entries, CMPID titles, coordinates
and native score properties unchanged; per-compound receptor structures remain
paired with their ligands. Merge completed queue manifests plus the finishing
queue, including each compound once, and exclude failed results. Publish through
a temporary MAEGZ and atomic rename before archiving the queue. A merge failure
retains the queue and committed scores for retry without calculation resubmission.
Missing successful native output is an error, not a reason to use docking/input
coordinates. Legacy completed batches copy their native output atomically to the
same final filename. This postprocessing does not launch new calculations.

## AmberTools prescreen before Prime

The 2026-10-12 user change adds optional Amber screening after completed Glide
and before Prime. See SPEC section 15 for confirmed scope and scientific choices.
When enabled: select final Glide top 200, minimize/score only those compounds
with Amber on the configured host (EGFR: t41), and claim its Prime top 50 by Amber score. Store `amber_gbsa_score` separately; keep Prime
`prime_gbsa_score`. Use `iterN/amber_gbsa/` and leave `iterN/prime_gbsa/` intact. The example
EGFR config disables it. Set `amber_gbsa = true` to enable it; the legacy
`amber-gbsa-enabled` key remains supported. Omitted Prime count is 200 without
Amber and 50 with Amber, and omitted Amber input count is 200. Explicit counts
override these defaults. Conflicting switches are rejected.
Iteration 0 also runs Amber before new Prime calculations when enabled, using all eligible supplied reference poses without docking-score/top-N filtering. Map source titles to CMPID in calculation copies. Bootstrap the default receptor with the existing grid extractor when absent. Existing Prime submissions are recovered first.
Maintain inherited remote locks, durable submission identity and source fingerprints;
never reset uncertain remote work or promote cached output into completion.
The synthetic real-calculator smoke test is not validation of target ranking or
128-core production throughput.


## Amber receptor PDB normalization

After every Maestro PDB export, retain `receptor.pdb` and normalize a separate
`receptor.normalized.pdb` using `receptor_pdb.py`. The standalone remote worker
bundles the same module and revalidates before receptor LEaP; all ligand LEaP
inputs also use the normalized PDB. Fixed-width changes preserve source identities,
coordinates and TER boundaries. NMA/ACE methyl names and ACE-connected H1 are
normalized; HIS ring hydrogens determine HID/HIE/HIP, and explicit states must agree.
Missing protonation, unexpected caps or unresolved conformers stop preparation.
Changes/errors are logged. LEaP cap template names are aligned to the normalized
PDB without changing parameters. `receptor-pdb-v1` is recorded in new inputs;
there is no topology cache. Stale products are removed only after locking, and
LEaP errors/missing or unreadable products prevent MM-GBSA. Keep saved runs intact.


## EGFR Amber host update

The EGFR TOML now selects `amber-ssh-host="t41"` and
`amber-conda-init="/home1/amber26/miniforge3/etc/profile.d/conda.sh"`.
Keep `/home1/amber26/envs/AmberTools26`, the remote work directory and 124 parallel
single-core calculations. The existing `environment_command` sources conda.sh,
activates the configured environment, sources `$CONDA_PREFIX/amber.sh`, then sets
native thread counts to one. No adapter/default changes are needed. REINVENT and
AiZynthFinder remain on tensor, and Schrödinger retains its separate host setting.
Preserve saved run identities/settings; do not move or resubmit existing jobs.

A noninteractive t41 probe verified the environment and required executables.
An isolated one-ligand EGFR smoke run exercised receptor normalization/LEaP,
AM1-BCC, parmchk2, complex LEaP, MMPBSA.py and result/log download with a finite
score, terminal marker and released lock. No production DB/job changes were made;
124-way throughput was not benchmarked.


## Latest Amber Glide preselection and restrained minimization

`amber-keep-count=200` now limits the Amber input population by final finite Glide
score (ID breaks ties), before excluding scored/failed/running rows. Restrict
Amber completion and new Prime ranking to that same population. Select Prime's
`prime-gbsa-input-count=50` by finite Amber score, then Glide score/ID. Preserve existing
outside scores, but exclude them from new Prime selection; recover legacy saved
all-pose runs and running claims intact. Iteration 0 remains exempt from the limit.

New Amber calculations restore original docking coordinates after charge typing,
then minimize the combined complex using `ntr=1`, `restraint_wt=2.0`, and
`restraintmask='!@H='` with original complex coordinates as restraint reference.
Evaluate MM-GBSA from the separate minimized restart, using the same snapshot for
complex/receptor/ligand energies, without adding restraint energy to MM-GBSA.
The current implementation uses 500 cycles (250 steepest descent), nonperiodic
GB igb=5/0.15 M salt; these cycle/solvent choices are implementation details pending
scientific evaluation. Reject missing/nonfinite/invalid final results and retain
minimization inputs/logs. Cycle-limit termination is completed execution, not proof
of convergence. New manifests record the versioned restrained-minimized protocol;
keep old results and saved workers intact. Prime's pose input flow is unchanged.
A one-compound t41 smoke run completed minimization and MM-GBSA; its mask selected
all protein/ligand heavy atoms, and the restart was the scoring input. The run hit
500 cycles before convergence; no production data/jobs were changed.


## Full calculation-stage elapsed times

After Generate, LigPrep, all Glide groups, selected Amber work or final Prime work completes
under the supervisor, print the full stage's wall time in HH:MM:SS.s. SQLite
`stage_timing` checkpoints the first supervised pass and verified completion per
iteration/stage, surviving polls/restarts and including waiting/recovery/downtime.
Finish only when existing DB and external-job completion checks both pass.
Failure/pause leaves the finish unset; retain the first verified finish on reruns.
Prime recovery starts its timer but does not finish the final Prime stage. Keep
worker-pass/native-job timings separate. Older pre-timer execution cannot be
reconstructed; measure from the first newly recorded supervisor pass.
The same completion line reports result counts and `wall/compound` throughput,
not an individual runtime. Prime archived single-compound logs add native job
mean/median/min/max with coverage. Amber new remote results add `elapsed_s`
(monotonic parameterization/minimization/scoring wall time, excluding shared
receptor setup/executor waits); old workers/cached results remain valid without
that field. Missing timings remain n/a; grouped LigPrep/Glide do not pretend to
provide per-compound runtimes. Summaries only read results and artifacts.



## Optional Amber mode — 2026-10-13

Amber is disabled by default, including in examples/egfr.toml. Default flow is
Glide top-200 directly to Prime. With `amber_gbsa = true`, use Glide top-200
for Amber and Amber top-50 for Prime. Omit `prime-gbsa-input-count` for mode-dependent
defaults; explicit counts and the legacy switch remain supported. Preserve
existing results and submitted work when changing configuration.

## Explicit Amber timeout retry (2026-11-02)

**Confirmed user request:** recalculate iteration 60's 413 unscored Amber
failures and score the available coordinates when the ten-minute limit is hit.

**Implementation choice:** `python -m graft.stages.amber_retry PROJECT --iteration 60
--source FINISHED_ARRAY_MANIFEST --watch` creates one durable owned retry array
in `07-amber_gbsa/timeout-retry`. Reverify original native termination and released
locks, source fingerprints and exact identities; select only unscored terminal
Amber failures from that finished array. Preserve existing successful scores and
already submitted Prime jobs. Pin a 600-second **minimization** deadline and a
separate one-hour Slurm allocation to allow parameterization and GBSA scoring.
This is an explicit retry policy; ordinary new/saved arrays keep their existing
scheduler limit and scientific protocol. It does not retroactively recover
coordinates that older timed-out tasks never saved.

The retry worker retains ff14SB/GAFF2, mbondi2, igb=5, saltcon=0.15, restrained
500-cycle minimization, original reference coordinates and single-frame MMPBSA
without entropy. Only retry inputs add native `ntwr=-10, ntxo=1`: a restart every
ten minimization steps. At deadline kill and wait for serial sander, select the
highest readable full-atom finite checkpoint, and score those exact coordinates.
The last saved checkpoint can precede the interruption by up to ten steps.
Incomplete checkpoints are rejected; absent valid coordinates, parameterization
errors and scientific failures never generate invented scores. Completed runs
continue to require native final energy and coordinate validation.

Persist completed-versus-time-limit provenance in result JSON, archived
`minimization_status.json` and the database's non-destructive `amber_retry_result`
journal, retaining the original failure message. A successful explicit retry
clears only its previous Amber failure marker and restores the compound's
appropriate state. Existing results and live/unrelated states are preserved;
conflicts roll back the batch. Collect native terminal results and logs before
retiring the retry manifest. Reconnection reuses its JobId and immutable inputs;
a completed retry is never implicitly submitted again. Existing production Prime
selections are not reset or replayed by this maintenance worker.

**Evidence/limits:** installed AmberTools26 on an allocated CPU node generated
periodic minimization restarts. A separate ACE-ALA-NME interruption smoke test
validated a readable finite checkpoint after killing sander at 0.5 seconds. This
checks native checkpoint mechanics, not ranking accuracy or equivalent scientific
quality of truncated versus fully minimized structures. The one-hour hard limit
can still fail exceptionally long preparation/scoring; those failures retain
their diagnostics and do not supply fabricated energies.


## Adaptive LibInvent elite MCS and alternate sites (2026-11-04)

**Confirmed user requirement:** for new automatic LibInvent generation snapshots
in adaptive mode, try a score-ranked prefix of the configured elites (EGFR: 10),
halving with integer division, e.g. 10 -> 5 -> 2 -> 1, when the MCS is unchanged.
Compare chemical retained-core graphs, independent of SMARTS spelling, atom order
and dummy labels. Keep explicit supplied inputs, pinned fixed cores, bootstrap
iteration 1 and all existing immutable snapshots under their existing rules.
At one elite, retain exactly the immediately preceding generation core and choose
chemically different attachment positions; do not adopt the whole elite or extend
its core by terminal-sidechain replacement. A dummy-bearing supplied core retains
its non-dummy MCS; attachment markers are replaced. Label swaps and molecular
symmetry alone do not establish different positions. If no valid alternative
exists, stop without creating a generation snapshot or changing scientific results.
This supersedes unchanged-scaffold fallback for new adaptive LibInvent snapshots.
Existing combichem/3D grow protocols and saved plans remain unchanged.

Implementation: enumerate actual single-bond, acyclic external boundaries of the
top-ranked elite's embeddings of the saved concrete retained core. Keep exactly
two terminal attachment markers, preserve the fixed docking core and retained
stereochemistry, reject undeclared branches, and require exact decomposition and
reassembly of the top elite plus at least two of the original selected elites.
Choose deterministically by canonical scaffold SMILES and freeze the result,
ranked IDs and attempt history in SQLite. No hydrogen decorators are invented.
If an automatic MCS has invalid attachment counts, reject the attempt and halve;
retain the validated conservative two-boundary extension for excess attachments
when it produces a different core. LibInvent MCS timeouts/invalid chemistry still
stop. TL uses the original selected population and original prior with its existing
minimum-two-pairs and vocabulary gates; reducing MCS inputs does not reduce the
configured elite count or duplicate a singleton for training. Tests establish
molecular graph behavior and restart recovery, not production TL or affinity.


## Fill MD GPU vacancies before GBSA postprocessing (2026-11-04)

**Confirmed user requirement:** for 24 selected molecules and 20 available MD
GPU slots, admit the remaining four MDs as soon as completed native MDs free
slots, before frame extraction/transfer or Prime GBSA processing. GPU MD and
CPU GBSA may overlap within the same independently supervised MD-GBSA worker.
The driver and its single-iteration sequential supervision are unchanged.

The worker first queries MD-only native status for all unresolved GPU owners
and jobs missing from progress.json, including lost-response recovery; it then
fills available GPU capacity. Running and uncertain launches reserve slots;
terminal native parent plus terminal children are required before returning a
slot. MD completion never publishes an ensemble score. New MD intent is saved
before SSH; unknown replies, unavailable status and orphaned launch artifacts
never authorize duplicate submission. A failed terminal MD also releases its
GPU slot while the existing ensemble failure/retry rules remain in force.
Only after admission does the worker extract/transfer all ready local-Prime
frames, submit/recover native Prime batches and commit validated full ensembles.
Cross-project Glide/Prime fences still govern CPU Prime submission, independently
of MD admission. Existing CPU budgets, GPU caps and scientific protocols remain.

Use a checksummed operational adapter under the frozen remote run's control/
directory. It verifies and imports that run's original native.py and uses its
exact workflow and native command; never overwrite frozen scientific helpers,
input settings, JobIds or protocol hashes to apply the scheduling change.
MD-only queries avoid downloading trajectories or extracting frames. Existing
runs can use the adapter at the next worker invocation. Tests cover 24/20
admission order, uncertainty reservations, lost progress/replies, exact command
recovery, parent/child completion and GBSA result/cleanup gates. Mock tests do
not measure production throughput or validate scientific MD/GBSA accuracy.


## MD-GBSA pending progress counts (2026-11-04)

**Confirmed user requirement:** driver MD-GBSA pending lines include original
compound counts for queued, running, calculated and db_committed, with db_pending
for calculated results not yet persisted. Counts read the frozen selection,
last worker progress snapshot and finite SQLite scores; no native jobs are queried
or launched by the driver. Calculated includes committed molecules and requires
all requested replicas to report complete MD+GBSA ensembles. MD-only completion,
frame transfer and pending/running Prime remain running. Missing submission
records count as queued (not a native JobServer assertion); corrupt snapshots or
missing selected rows are unknown. Failures have a separate optional count.
Committed SQLite results override stale/missing progress. Preserve the pending
reason and elapsed time, including nonzero worker exits. Logging changes never
commit scientific results or change workflow completion gates.


## Reuse existing LibInvent attachment positions when alternatives exhaust (2026-11-04, latest choice)

**Confirmed user requirement:** keep the ranked 10 -> 5 -> 2 -> 1 MCS policy
and first prefer chemically different valid attachment positions at the terminal
fallback. When no valid different positions exist, reuse exactly N-1's saved
MCS and attachment-bearing scaffold and continue new decoration generation.
This supersedes the earlier stop-on-no-alternative rule for new adaptive
LibInvent snapshots. Never use the singleton's full structure as a new MCS.
Validate the saved core, its two labelled single-bond attachments and fixed
docking core. Require exact decomposition/reassembly of the top elite and at
least two of the originally selected elites before site reuse; missing, invalid
or incompatible preceding scaffolds still stop. Preserve the original selected
training population, vocabulary checks, minimum-two usable TL pairs and TL from
the original configured prior; no synthetic singleton training duplication.
Save source=previous_mcs_reused_attachments, fallback_reason=
no_alternate_attachment_points, compatible elite IDs, previous scaffold and
ranked attempt history (terminal status=reused_attachments). Logs explicitly
identify the reused positions and continued decoration generation. New snapshots
use ranked_halving_alternate_attachments_v2 and freeze the choice on restart.
Existing snapshots/results/jobs and the independent combichem protocols remain
unchanged. RDKit structure validation is not a production TL/sampling validation.


## Best-elite generation core with internal sidechain attachments (2026-11-04, latest requirement)

**Confirmed user requirement:** when automatic ranked MCS reduction reaches its
single-best-elite fallback, construct a new generation core from that molecule
with at least two attachment points located in the sidechains attached to the
preceding scaffold. Retain the ranked 10 -> 5 -> 2 -> 1 search; LibInvent supports
exactly two attachments, satisfying that minimum, one in each old sidechain.
This supersedes terminal same-core position search and unchanged-position reuse
for new automatic LibInvent snapshots. Do not invoke single-molecule FindMCS or
adopt the entire best molecule without decoration boundaries.

Implementation: use the preceding concrete scaffold's attachment-aware best-elite
embedding, preserving both the previous retained core and fixed docking query.
Enumerate terminal single-bond, non-ring cuts whose retained and distal endpoints
both belong to the same old sidechain. Exclude the original core-sidechain bonds.
Keep each sidechain root in the new core and replace a distal component by that
sidechain's original label 0/1. Never split a ring or change bond order, charge,
isotope or retained stereochemistry. Rank candidates by largest retained heavy
core, then bond indices in the canonical best molecule for deterministic ties.
Build each candidate using the existing validated terminal-fragmentation helper;
require exact isomeric decomposition/reassembly of the best molecule and at least
two of the original selected training elites. Select the first passing candidate.
MCS construction uses only the best molecule; training support does not redefine
that source. TL still uses the full originally selected preceding-iteration elite
population, the original prior, vocabulary checks and minimum-two usable pairs.
Never duplicate the best molecule or silently skip TL to satisfy that minimum.

Persist source=single_elite_sidechain_mcs, source_ids=[best_id], new retained-core
SMARTS and two-site scaffold, eligible cut groups, selected cuts and canonical
source molecule, compatible elite IDs, prior scaffold and core atom indices.
Use policy ranked_halving_single_elite_sidechains_v1 and the same named terminal
attempt status. Logs show the best ID and internal-sidechain attachment choice.
Missing internal cuts (e.g. a one-atom halogen or an unsubstituted cyclic sidechain)
or insufficient compatible training data stop explicitly rather than reusing old
positions. Saved snapshots, generated compounds, submitted jobs and old protocol
records remain immutable; this applies only to new snapshots. Independent
combichem generation behavior is unchanged. Molecular regression tests and a
read-only reconstruction from real EGFR elites validate graph behavior, not
production TL/sampling, docking or GBSA accuracy.


## Reuse previous LibInvent scaffold on insufficient sidechain training support (2026-11-04, latest correction)

**Confirmed user requirement:** allow previous-scaffold reuse in the reported
iteration-3 case: valid best-elite internal-sidechain scaffold candidates exist,
but none supports at least two of the originally selected training elites.
This narrowly supersedes the insufficient-training stop in the preceding
best-elite requirement. Keep ranked halving and prefer a validated new core.
Only after all candidates fail and at least one exactly reconstructs the best
elite but lacks a second compatible training elite, reuse exactly N-1's saved
MCS and attachment-bearing scaffold. Validate its core, two labelled terminal
single-bond attachments and fixed docking query; require exact isomeric
reconstruction of the best elite and at least two original training elites.
Missing internal cuts, invalid chemistry/saved scaffolds, MCS timeouts and
insufficient training support even for the previous scaffold still stop.

Persist source=previous_mcs_reused_attachments, fallback_reason=
insufficient_sidechain_training_elites, compatible elite IDs, original ranked
source IDs, previous scaffold and terminal attempt status=reused_attachments.
New ranked snapshots use policy ranked_halving_single_elite_sidechains_v2.
Logs identify the training-support reason and reuse of existing attachment
positions for continued decoration generation. Preserve all original selected
TL inputs, original-prior restart, minimum-two usable pairs and vocabulary
checks. Existing immutable snapshots, results, jobs and combichem are unchanged.
Regression coverage includes the reported EGFR lactam elite, strict reuse
validation, snapshot recovery and original-prior TL/sampling with reuse.
These checks do not validate production scientific calculations or affinity.

**Verification:** 197 affected regressions passed. The full suite reported
1,893 passed, seven skipped and the previously documented unrelated failure in
`test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself`.
Editable installation, compileall, package/CLI imports and diff whitespace checks
passed. Read-only reconstruction of the actual iteration-2 MD-ranked top ten
confirmed exact previous-scaffold reuse with all ten elites compatible. No
production database rows, saved snapshots or native jobs were changed or launched.


## Reuse previous LibInvent scaffold when internal sidechain cuts are unavailable (2026-11-04)

**Confirmed user requirement:** extend validated previous-scaffold reuse to the
reported iteration-4 failure, where the best elite has a one-carbon methyl
sidechain with no eligible internal single bond. This supersedes the earlier
missing-internal-cut stop rule for new adaptive LibInvent snapshots. Keep ranked
10 -> 5 -> 2 -> 1 search and prefer a validated new core when possible.

At the terminal best-elite fallback, if either old sidechain has no eligible
internal cut, reuse exactly N-1's saved MCS and attachment-bearing scaffold.
Retain saved-core, two labelled terminal single-bond attachment and fixed docking
query validation. Require exact isomeric decomposition/reassembly of the best
elite and at least two originally selected training elites. Invalid saved
scaffolds/chemistry, incompatible best elites, insufficient previous-scaffold
training support and MCS timeouts still stop. Do not split rings or substitute
old boundary cuts to manufacture new internal attachment positions.

Persist source=previous_mcs_reused_attachments, fallback_reason=
missing_internal_sidechain_cut, fallback_sidechain_label, compatible elite IDs,
original ranked source IDs, previous scaffold and terminal attempt status=
reused_attachments. New ranked snapshots use policy
ranked_halving_single_elite_sidechains_v3. Logs identify the missing-cut reason
and continued new decoration generation using existing attachment positions.
The insufficient-training fallback retains its existing validation and reason.
Preserve the original selected TL population, original-prior restart, vocabulary
checks and minimum-two usable pairs. Existing immutable snapshots, compounds,
results, submitted jobs and independent combichem protocols remain unchanged.
Regression tests validate molecular graphs/workflow recovery, not production
TL/sampling or affinity.


**Verification:** 233 affected scaffold, vocabulary, iteration and combichem
regressions passed; compileall, package/CLI imports and diff whitespace checks
passed. Read-only reconstruction of the actual iteration-3 MD-ranked top ten
reproduced 10 -> 5 -> 2 -> 1 with missing_internal_sidechain_cut at label 1 and
all ten elites compatible with the exact preceding scaffold. During verification,
the existing supervisor picked up the change, saved the v3 fallback snapshot,
generated iteration 4's 10,000 compounds and advanced its 5,000 druglike passes
to ligprep_running. No manual database edits, resets or job submissions were
performed. This is workflow evidence, not scientific accuracy validation; the
full test suite was not rerun for this extension.


## Reject unusable adaptive LibInvent MCS candidates (2026-11-05)

**Observed runtime bug:** iteration 2 accepted a different top-two MCS whose
attachment-bearing scaffold could not decompose either source elite, leaving
zero usable TL pairs. A matching common substructure alone does not establish
valid attachment boundaries or nonempty decorators.

**Implementation repair:** before accepting a different automatic ranked MCS,
require exact isomeric decomposition/reassembly of every elite in that ranked
prefix. Record `elite_decomposition_failed` and continue the existing halving
and validated best-elite/previous-scaffold fallback when this check fails.
Preserve saved immutable snapshots, original ranked TL inputs, vocabulary and
minimum-two checks, original-prior restart and fixed docking constraints.
This validation applies to new snapshots; it does not reset saved plans or jobs.
Regression tests include the reported EGFR top two and undeclared branches.
Read-only reconstruction of the actual top ten selects the validated previous
scaffold; all ten reconstruct exactly and pass the saved vocabulary checks.
These checks do not validate external TL/sampling or affinity.

**Verification:** 148 focused regressions passed. The full suite reported 1,901
passed, seven skipped and the previously documented unrelated
`test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself`
failure. Compileall, package/CLI imports and diff whitespace checks passed.
No production database, saved plan, calculation or submitted job was changed.


## LibInvent hydrogen decorations (2026-11-05)

**Confirmed user requirement:** support an unsubstituted attachment position as
hydrogen in LibInvent elite decomposition, training and sampled-product assembly.
This supersedes the earlier prohibition on hydrogen decorators for this LibInvent
path. Keep exactly two declared attachment sites and the original prior, selected
elite population, vocabulary gate and minimum-two usable training-pair gate.

**Implementation choice:** represent an ordinary hydrogen decoration by a whole
fragment `*`, e.g. `*|*CN1CCCC1`. This uses the original prior's existing tokens;
do not introduce unsupported `[H]` training tokens or empty fields. After the
existing heavy-atom decomposition fails, match a temporary explicit-H copy, keep
all undeclared-branch/contact/overlap checks, and accept only exact canonical
isomeric decomposition/reassembly. Do not replace isotope-labelled hydrogen by
ordinary H or change charge, bond order or stereochemistry to make a pair fit.
Automatic LibInvent candidate validation uses the same hydrogen-aware checks.
Shared combichem decomposition, fragment admission and assembly remain unchanged.

Both local and SSH LibInvent sampling/TL run through the standalone launcher.
Within that process only, expand a whole decoration `*` to native `*[H]` before
REINVENT assembly and remove ordinary explicit H from the product. Ordinary
fragments retain native behavior; blank fields remain invalid. Do not edit the
installed REINVENT package, prior, previously uploaded workers or saved scaffold
snapshots. A saved scaffold can now produce valid new TL pairs without replacing
its snapshot. Every new SSH invocation uploads its launcher into its own run
folder; preserve existing remote JobIds, directories and recovery semantics.
Models trained with this convention require this launcher when sampling.

Tests cover the reported two EGFR elites, one/two hydrogen decorations, same-atom
sites, isotope and stereochemical preservation, undeclared branches, connected
sidechains, invalid valence/fragments, training vocabulary and launcher routing.
These graph/runtime checks do not establish affinity or production sampling quality.

**Verification:** full suite: 1,920 passed, seven skipped, with the previously
recorded unrelated real-fragment collision assertion failure in
`tests/test_combichem_3d.py`. Compileall, package/CLI imports and diff checks passed.
A read-only CPU probe of the installed LibInvent original prior accepted both
reported H-containing pairs, produced finite likelihoods and gradients, and
reconstructed both molecules through the native assembly function with the
process-local adapter. No prior/model was saved by that probe. The existing
production supervisor independently loaded this change, accepted two of ten
selected elites without rewriting the saved scaffold, completed TL/sampling and
inserted new iteration-2 compounds. This is observed workflow compatibility,
not docking/GBSA accuracy or a guarantee of sampling diversity/quality.
