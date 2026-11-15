# T1–T9 current progress

Latest bug-fix verification: **2,904 passed / zero failed / seven skipped**.
See [verified Glide input recovery](../diagnostics/GLIDE_INPUT_RECOVERY.md).

Updated 2026-11-15 KST. This table describes the verified **local working tree**,
including previously existing uncommitted features. It is not an assertion that
all runtime changes have been committed or deployed. Historical phase reports
retain their original results; this is the current entry point.

| Phase | Current status | Implemented and retained scope |
|---|---|---|
| T1 | Complete within safe scope | Four unused imports removed; two fingerprint-sensitive candidates and legacy APIs retained |
| T2 | Main implementation applied | SMILES/fingerprint/Tanimoto ownership separated; legacy mutable binding and canonicalizer overrides retained |
| T3 | Main implementation applied | DB connection/schema/state/small-query ownership moved; adapters, some business SQL and core reverse hooks remain |
| T4 | Main implementation applied | TOML loading in config; initialization in project; original configure API and ordering retained |
| T5 | Complete within equal-contract scope | Three JSON writers shared; incompatible durability/standalone helpers kept separate |
| T6 | Six orchestration boundaries applied | Prime, training, MD, scaffold, Glide, driver; deeper native loops/scientific algorithms retained |
| T7 | Characterization expanded; runtime transition held | 17 new cases cover selection, singleton legacy binding, writer forwarding, failure cleanup and real fork inheritance |
| T8 | Local types/contracts documented | Existing narrow annotations retained; payload/ownership guide added; no global strict-check or broad typing rollout |
| T9 | Refreshed verification and documentation | Current full suite 2,904 passed / 0 failed / 7 skipped; science/live multi-host recovery remains unverified |

## Next boundaries

- Current `database.connect` and several query/state functions deliberately call
  core compatibility hooks. Direct leaf calls would remove tested monkeypatch
  seams; define that compatibility policy before removing the remaining cycle.
- Native SQLite context commits/rolls back and does not close the connection.
  The legacy adapter closes on exit. Do not unify these lifetimes implicitly.
- `_elite_training_rows` returns ordered `(usable, skipped)`: usable tuples are
  `(id, name, smiles, iteration, docking_score, score, heavy_atoms,
  ligand_efficiency, scaffold, decorators)`; skipped tuples are
  `(id, name, invalid_tokens_json, scaffold, decorators, reason)`. The caller
  captures decomposition/token callbacks before loading the prior vocabulary.
- `_freeze_new_md_selection` returns the original dictionary with `ids` and
  `settings`; remote compatibility/local preparation precede the existing atomic
  readiness/rank/claim transaction. A typing cleanup must not add coercion/schema
  validation or change None/exception behavior.
- `_recover_prime_gbsa_work` retains three mode-specific pollers and legacy JobId
  retirement ordering. It must not substitute cached output for native completion.
- Legacy distributed install remembers the first worker process-wide, local
  fallback forwards a non-None writer, Slurm omits it, and SSH temporarily assigns
  the child module search callable until child exit. These actual contracts are
  characterized, not endorsed as the future design. Explicit callable selection
  still requires a versioned/fingerprinted deployment and equivalent recovery,
  writer/claim and child-process behavior. No T7 runtime switch was made here.

## Release state

The follow-up is organized as six commits: the previously validated source/test
baseline (including its existing feature dependencies and earlier T2–T6 refactors),
MD/clash test expectations, process-exit checks, pytest isolation, T7 contracts,
and final evidence/documentation. The subsequent user push instruction authorizes
publishing the coherent current project source. Target-specific example changes,
input deletions, production outputs/databases and macOS metadata are excluded.

A smaller test-only Git candidate passed 2,624 cases with seven skips. Its runtime
lacked the existing local features, so its count is separate from the full current
working-tree result. The final complete source/test Git candidate is exported and
verified separately. The complete wheel/source archive remains an unpublished,
SHA256-identified package artifact; Git push does not install it on production.
See [the refinement report](REFINEMENT_REPORT.md) for all verification boundaries.
