# Architecture

Changed-source Glide recovery is restricted to original JobIds with native input
SHA256 verification, with no new claims/submission or global checksum rewrite.
[Incident and limits](diagnostics/GLIDE_INPUT_RECOVERY.md). Latest full suite:
2,904 passed, zero failed, seven existing skips.

Refinement verification before this Glide repair: 2,882 passed / zero failed / seven skipped. The formerly known
three failures were repaired in separate authorized test-only work; runtime source
was unchanged. See [current progress](refactoring/PROGRESS.md) and
[refinement report](refactoring/REFINEMENT_REPORT.md).

This describes the current worktree after the applied T2–T8 changes and the
2026-11-15 T6/T2 and T3/T4/T6 follow-ups. See the [latest applied report](refactoring/T346_FINAL_REPORT.md). See [T6](refactoring/T6_RESTART_REPORT.md) and
[T2](refactoring/T2_RESTART_REPORT.md) for the applied scope and retained compatibility bindings. It
adds no scientific requirements. [SPEC](SPEC.md) remains authoritative, including
its evidence/uncertainty labels and unresolved historical scheduling ambiguity.
[The final refactoring report](refactoring/T9_FINAL_REPORT.md) distinguishes applied
changes from isolated, deployment-held candidates.

## Workflow and ownership

SQLite coordinates independent stage subprocesses. The driver chooses one
iteration and supervises its stages sequentially; workers do not launch or
coordinate other workers. Scientific calculations stay inside workers/helpers.
This does not prohibit internal parallel calculation: Glide submits reference
groups while its collector immediately persists verified group results, and MD
workers retain their existing native/CPU overlap and resource limits.

```text
CLI / driver (iteration selection, subprocess supervision, completion gates)
  └─ stage subprocesses, in the configured sequence
       generate → druglike → synthetic feasibility (optional)
       → LigPrep → Glide → Amber (optional) → Prime (optional)
       → MD (optional, including saved-work recovery) → FEP (optional)
         ↕ atomic claims, frozen selections, committed results, durable timers
       SQLite ← database.py / existing _core compatibility primitives
         ↕ durable JobId records and verified native/remote status
       calculation helpers / native job systems
```

The diagram is a role/order sketch, not a complete transition table. Iteration 0
has reference-specific scoring stages. Disabled stages may still require saved
work recovery; a completed reference-score cache can bypass native calculation
through its explicit verified cache protocol. Neither a zero worker exit nor
cached output establishes native completion. See driver.iteration_stages,
stage_completion_reason and external_completion_reason for the actual gates.

`states.py::CompoundState` is the single compound state vocabulary. Reference rows
and generated molecules have different origins; they are not a universal linear
reference→generated transition. Optional screening, Amber, Prime, MD, FEP and
failure/retry paths depend on configuration and persisted selections. Permanent
failures retain failed_stage and failure_message. MD selection/progress/result
records distinguish calculated, committed, failed and uncertain work; state alone
is insufficient to decide progression.

## Module and implementation boundaries

| Area | Current responsibility |
|---|---|
| cli.py / driver.py | CLI compatibility, project/iteration supervision, authoritative completion checks; driver._report_pending_stage owns poll diagnostics; _report_completed_stage owns verified-completion reporting |
| stages/ | Stage orchestration and native submission/recovery. Glide._claim_reference_groups owns claim/filter; _recover_reference_groups reconciles saved native groups before unchanged submission/collection |
| database.py | SQL connection/schema/state primitives and small legacy queries, migrations, atomic claims, frozen selections and results. Some business SQL/legacy adapters remain in core |
| config.py | TOML loader and existing validators/defaults/aliases/path/backend settings; fresh import is independent of core. configure facade imports core only on invocation |
| _core.py | Compatibility APIs (DB and TOML forwarding), configure namespace/log setup, FP_GENERATOR and remaining validated prototype/legacy runners |
| project.py | Migration/reference/cache/schema/output initialization, invoked by core.configure at the existing point |
| md_gbsa.py | Independent MD worker; _md_upstream_ready isolates gates, _freeze_new_md_selection owns check-before-freeze input preparation; saved-job polling and persistence unchanged |
| chemistry/scaffold.py | _create_iteration_scaffold owns new snapshot construction; _validated_docking_smarts validates attachments/core before SQLite and file publication |
| chemistry/smiles.py | Canonical isomeric SMILES implementation with the existing input guard and RDKit exception behavior |
| chemistry/fingerprints.py | Fresh Morgan factory and pure Tanimoto implementation; only RDKit/stdlib/caller-provided fingerprint callable |
| chemistry/similarity.py | Existing Tanimoto facade; resolves the current _core.FP_GENERATOR for each fingerprint call |
| _job_record.py | Shared default-JSON/.tmp/replace publication used only by the three package-local Prime/LigPrep/Glide wrappers |
| native/remote helpers | Existing separately deployed protocols, checksums and calculations; their implementation/persistence policies remain separate |

Applied dependency changes are deliberately limited:

```text
existing callers → _core.canonicalize_smiles → chemistry.smiles → rdkit.Chem
_core.FP_GENERATOR initialization → chemistry.fingerprints.create_morgan_generator
similarity.tanimoto facade → chemistry.fingerprints.tanimoto_with_fingerprint
Prime._save / LigPrep._save / Glide.save_record → _job_record → stdlib

_core DB facades → database (implementation owner, deferred imports)
database → _core (legacy dynamic connection/adapter/canonicalizer hooks)
_core.load_project_toml → config.load_project_toml → existing config helpers
_core.configure_project → project.initialize_project → database/core hooks
config.configure_project → _core (deferred compatibility facade)
```

Prime new-batch file preparation is isolated in _core._prepare_prime_gbsa_input_files;
LibInvent pair writing is isolated in _core._write_libinvent_training_pairs. Existing
record recovery is separated in _recover_prime_gbsa_work, training row preparation in _elite_training_rows; token-error/partial-file behavior and scientific selection stay unchanged.

The compatibility wrapper keeps the old function path/signature and database's
dynamic lookup. FP_GENERATOR remains the same module-level shared object with
Morgan radius 2 / 2048 bits; similarity's per-molecule lookup and fingerprint settings do not
change. The factory now owns configuration in chemistry.fingerprints, while core
constructs the compatibility binding at the original import/reload point. The pure
Tanimoto API accepts an explicit fingerprint callable; it and the SMILES leaf import
independently of workflow initialization.
The existing core/database logical cycle remains; deferred imports currently
avoid an initialization error. This work does not claim to eliminate that cycle.

## Persistence and identity

One compound row represents one original molecule, preserving CMPID_<SQLite id>
through structure processing. Canonical identity/dedup preserves isomeric SMILES,
charge, isotope and atom-map behavior; it does not use Tanimoto equality as molecule
identity. LigPrep-state docking selection keeps the lowest score and its matching
pose. Scientific parameters, pose ancestry, MCS/decoration rules and original-prior
TL behavior are unchanged.

SQLite uses the existing WAL, 30-second busy timeout, foreign keys and atomic claim
transactions. Migration remains non-destructive. Connection/context behavior is
unchanged: Python SQLite context handling is a transaction boundary and is not a
new automatic-close API. Status still performs its documented initialization and
legacy metadata backfill before reporting. Final-score readiness/ranking/claim
transaction boundaries and existing-score ranking positions are unchanged.

Prime/LigPrep/Glide JSON wrappers keep their old file names and dynamically resolve
their existing path helpers. Shared publication writes json.dumps with its existing
defaults to .tmp, then replaces. It does not create directories, append a newline,
fsync or swallow exceptions. Amber's indented/newline/.pending format and fsyncing
Slurm/native policies remain distinct. Failed publication retains the old record
and its existing temporary-file behavior; no new crash-durability guarantee is made.

## Source fingerprints and deployment

Source SHA256 is part of restart compatibility. CReM/standalone growth hash
combichem3d/stage sources; Slurm also hashes combichem*.py, config.py,
database.py and amber_slurm_control.py. Remote/native helper guards remain
separate. This follow-up **changes config/database source hashes**. The other
38 inspected protected source files, native payloads, checks and allowlists are
unchanged. Existing unfinished Slurm work may reject the new installation.

The user authorized the current program to stop during this deployment. This does
not permit data loss, resetting jobs, rewriting manifests or bypassing hashes.
The original source (including pre-existing user edits) is archived at the original
root, with checked dry-run restoration instructions in
[RECOVERY](refactoring/t346/RECOVERY.md). A synthetic same-path real-signature probe
verified old != new and old == restored. Live recovery and concurrent mixed-version
work have not been validated. The prior [held patches](refactoring/held/) remain
historical evidence and must not be reapplied to the current sources.

The new project.py and earlier chemistry leaves/_job_record must ship with the
package; wheel content was checked. The current signature coverage does not
necessarily include all transitive dependencies. No production DB/manifest/native
helper/result was modified, reset or resubmitted.

## Verification limits and remaining debt

The old three baseline failures are resolved by explicit verbose-test setup and a
deterministic artificial self-clash fixture. The production behavior and all
original assertion purposes remain. Seven Torch-dependent launcher cases remain skipped. Synthetic RDKit,
SQLite and mocked recovery tests validate observed contracts, not native scientific
accuracy, live restart equivalence or multi-host deployment.

Remaining core business SQL/legacy adapters, deep native Prime/MD polling/result loops,
scientific MCS/decoration algorithms and distributed install/uninstall global mutation
remain explicit technical debt. An unchanged Amber timeout test exposed an independent
/proc existence/read race once; original/candidate diagnostic reproduction and successful
rechecks are documented without changing its assertion. [T2–T9 reports](refactoring/T9_FINAL_REPORT.md) record the tested scope,
held work, API/fingerprint evidence and follow-up constraints.
