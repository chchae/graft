# Architecture

This describes the current implementation after the applied T2–T8 changes. It
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
| cli.py / driver.py | CLI compatibility, project/iteration supervision, authoritative completion checks; driver._report_pending_stage owns existing poll diagnostics only |
| stages/ | Stage orchestration and native submission/recovery. Glide._claim_reference_groups owns the unchanged per-group claim/filter loop |
| database.py | Most SQL, migrations, atomic claims, frozen selection and result persistence. Some legacy primitives/queries remain in _core and orchestration |
| config.py | Setting validators/defaults/aliases/path/backend settings; public loader/configure functions still delegate to _core in the applied tree |
| _core.py | Compatibility APIs and remaining validated prototype implementation, including project initialization, DB primitives, FP_GENERATOR and legacy runners |
| chemistry/smiles.py | Canonical isomeric SMILES implementation with the existing input guard and RDKit exception behavior |
| chemistry/similarity.py | Existing Tanimoto helper; dynamically reads _core.FP_GENERATOR |
| _job_record.py | Shared default-JSON/.tmp/replace publication used only by the three package-local Prime/LigPrep/Glide wrappers |
| native/remote helpers | Existing separately deployed protocols, checksums and calculations; their implementation/persistence policies remain separate |

Applied dependency changes are deliberately limited:

```text
existing callers → _core.canonicalize_smiles → chemistry.smiles → rdkit.Chem
Prime._save / LigPrep._save / Glide.save_record → _job_record → stdlib

database → _core (existing canonicalizer/connection/state primitives)
_core → database (existing deferred imports)
config → _core (existing loader/initialization facades)
```

The compatibility wrapper keeps the old function path/signature and database's
dynamic lookup. FP_GENERATOR remains the same module-level shared object with
Morgan radius 2 / 2048 bits; similarity's lookup and fingerprint settings do not
change. The new chemistry leaf imports independently of workflow initialization.
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

## Source fingerprints and deployment-held candidates

Source SHA256 is part of restart compatibility. CReM/standalone growth hash their
combichem3d chemistry/stage sources; Slurm additionally hashes combichem*.py,
config.py, database.py and amber_slurm_control.py. Remote dispatch also records
adapter/helper digests; native helpers have separate implementation guards.
A trivial source change can invalidate saved work. Fingerprint guards, allowlists,
manifests and stored DB/checksum values have not been altered.

The following candidates were implemented and tested only in an isolated tree:

* T3: database.status_rows(conn) owns CLI aggregate SELECTs using the same
  caller-owned connection/context and unchanged output.
* T4: config owns TOML loading, _core retains a forwarding loader, and config's
  initialization facade imports core only when called.

They are available as [held patches](refactoring/held/) and **are not present in the
applied runtime tree**. Their database/config edits change Slurm source hashes;
existing-job compatibility needs a separate deployment transition decision. They
must not be enabled by modifying saved manifests or bypassing source validation.

Applied files are outside the source lists inspected in this audit, and the
included source files/native helper payloads are unchanged from the starting tree.
That does not establish that every transitive dependency is covered by the current
fingerprint policy. New chemistry/smiles.py and _job_record.py must be included in
package deployment. No production jobs, databases, native helpers or outputs were
replaced, reset or resubmitted by this refactoring.

## Verification limits and remaining debt

The full suite retains the three known baseline failures (one synthetic molecular
self-clash expectation, two MD pending-log expectations); they are neither hidden
nor changed. Seven Torch-dependent launcher cases remain skipped. Synthetic RDKit,
SQLite and mocked recovery tests validate observed contracts, not native scientific
accuracy, live restart equivalence or multi-host deployment.

Core/DB ownership, project initialization side effects, large Prime/MD and scaffold
orchestration, and distributed install/uninstall global mutation remain explicit
technical debt. [T2–T9 reports](refactoring/T9_FINAL_REPORT.md) record the tested scope,
held work, API/fingerprint evidence and follow-up constraints.
