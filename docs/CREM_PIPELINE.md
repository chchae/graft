# CReM generation with the scored GRAFT pipeline

Use [`examples/egfr-crem-pipeline/egfr.toml`](../examples/egfr-crem-pipeline/egfr.toml)
for the new mode. Its output is separate from the existing standalone EGFR growth
project. Do not point it at a production or standalone growth output.

```bash
python -m pip install -e '.[crem]'
graft run examples/egfr-crem-pipeline/egfr.toml
```

The existing driver supervises **one GRAFT iteration per invocation**. Invoke the
same command again to resume unfinished work or select the next iteration. This
mode preserves saved results/jobs on restart; it does not invoke the standalone
`graft combichem-3d` command or clear its output.

Do not run `graft combichem-3d` with this pipeline TOML: that command performs
standalone growth and has no scoring stages. It now rejects top-level
`generation-method="crem"` before dispatch, output creation or reset and prints
the correct `graft run` command. Existing standalone results remain exportable
with `--export-only`.

If a standalone calculation was accidentally run after iteration 0, keep both
DBs and its output. From the project folder, use `graft run egfr.toml --iteration 1`
to select the pipeline's iteration 1. The normal driver checks and recovers any
remaining reference work before proceeding. Standalone internal-cycle results
are not automatically imported as scored pipeline iterations.

Iteration 0 keeps the existing reference calculation protocol unchanged. Starting
at iteration 1, the sequence is:

1. CReM single expansion, methyl probe and constrained 3D collision filtering.
2. Whole-iteration Drug likeness, then LigPrep and constrained SP Glide.
3. Existing Amber, Prime and MD-GBSA selection/calculation stages.
4. The next invocation selects the next iteration's parents by completed N-1
   MD-GBSA results.

The first parents are the **bare fixed frame** extracted from `reference-mcs`.
Each passing position gets up to `sidechains-per-site` compatible candidates;
each child adds exactly one fragment at one position. Existing `[*:N]` branch
addition budgets and protection of the fixed frame remain in force. Later parents
retain their saved CReM coordinates and atom provenance. Their MD-GBSA scores
choose identities; this is not a conversion of MD/Glide coordinates into an
inferred growth history. The fixed docking constraint excludes attachment dummies
and remains compatible with the original reference poses. Downstream LigPrep and
Glide keep the project's original CMPID identity handling and best-state pose
selection.

Use top-level settings for GRAFT progression:

```toml
generation-method = "crem"
max-iteration = 10
target-count = 10000
elite-count = 1000

[combichem-3d]
generation-backend = "crem"
crem-db = "/home1/crem/database/chembl22_sa2_hac12.db"
crem-radius = 3
sidechains-per-site = 1000
sidechain-min-heavy-atoms = 3
sidechain-max-heavy-atoms = 8
processes = 116
conformers = 2
torsion-trials = 2
optimization-iterations = 50
core-constraint = "fixed"
```

The complete example also supplies the required grid/reference, Amber, Prime,
MD and host settings. Local paths resolve relative to the TOML; remote compute
paths retain their original interpretation. CReM calculators run locally, while
the downstream stages keep their existing local/SSH/Slurm settings. This backend
does not require a REINVENT prior/runtime or transfer learning.

Do not put `max-iteration`, `elite-count`, `elite-rank-by`, `target-count`,
`stop-mode` or `out-dir` in `[combichem-3d]` for this mode. There are no inner CReM
cycles. Generation property filters/pruning and adaptive elite MCS overrides are
rejected; the common Drug likeness stage performs that filtering after generation.

`target-count` counts novel 3D-admitted molecules **before Drug likeness**.
CReM context matching may supply fewer fragments than the cap. If all parents'
pools finish before the target, the main SQLite DB records the actual complete
population and permits the remaining stages to continue. A worker error or time
limit preserves pending work and does not masquerade as exhaustion. A completed
zero-candidate search stops progression. If no finite MD elites remain, next
iteration generation stops explicitly.

`elite-count` is an upper bound on finite completed MD-scored parents. For example,
the supplied policy scores at most 24 compounds with MD, so at most 24 parents can
be selected even when `elite-count=1000`. No lower-scoring-source fallback is used.
A missing selected parent's CReM pose/history blocks generation instead of silently
selecting another parent.

The supplied EGFR example preserves the existing configured policy: Glide top 500
for Amber, Amber top 100 for Prime, Prime top 24 for MD, 5 ns and 20 snapshots.
Iteration 0 retains its existing special reference handling. Existing rank/failure
fallback rules within the scoring stages are unchanged. FEP remains an explicitly
configured optional export/import stage; the new backend does not submit FEP.

Each iteration has an isolated CReM calculator DB/journals under
`iterNN/02-generate/crem/`. Its local pose IDs are calculator bookkeeping; the main
project DB assigns the authoritative CMPID used by LigPrep and all score stages.
The main DB stores fixed-frame provenance and completion atomically, so importing
or restarting does not duplicate molecules or promote incomplete work. Generated
populations and parent selections are frozen. Use a fresh output when changing
scientific inputs; do not delete saved jobs or results to force regeneration.

Synthetic tests exercise real small CReM fragment chemistry and workflow recovery.
Artificial scoring values test ordering and gates, not real scientific accuracy.
No native EGFR docking, Amber, Prime or MD smoke test has been run for this backend.
