# Glide zero-pose timeout recovery failure

The iteration-7 ref_215952 request contains zero pose inputs and one excluded
native subjob. Its archive maps native indices 60–88 to 15 original compounds.
The collector attempted to rename best_poses.pending.maegz after appending zero
structures. The installed Schrödinger 2026-1 writer creates no file in this case.

The old synthetic writer created a JSON file even with no structures and masked
the native behavior. Making this fake lazy reproduced six new empty-output
failures and the existing zero-pose DB integration regression (7 failed,
45 passed). The missing-file regression for a nonempty selection already passes.

The implementation now guards only pose writing/publication with `if poses`.
Empty score/report publication and existing timeout/no-pose transaction handling
continue unchanged. Previously published poses are preserved. Nonempty writer
failures still propagate before publishing scores/report.

Verification artifacts and commands are in /tmp/graft-glide-empty-e3n13nd7/:
native-before.log reproduces the exact reported traceback using production input
paths but an isolated output; native-after.log shows success under the same
installed suite. The after report retains native boundaries and source checksums.
89 focused timeout, pose selection, changed-input recovery and unavailable-status
tests pass. Full-suite results are recorded separately below when complete.

No production DB/manifests/native inputs/results were manually changed for
verification; no native calculation was submitted, stopped or restarted. Only
the collector implementation and its test helper/regressions change. Existing
user changes, including the earlier continuous-MD controller update, are retained.

Final verification: full baseline 2,909 passed / 7 skipped; candidate 2,916 passed /
7 skipped. All existing individual node outcomes match; seven added regressions
pass, with zero new failures. The same 330 existing multiprocessing warnings
remain. Compileall and package/CLI/collector imports pass. All 40 fingerprint-
protected sources match the pre-task snapshot; the collector is the only runtime
source changed. No checksum guard is bypassed.

After atomic deployment, the existing driver's automatic retry published the
real group's report (zero scores, 15 excluded timeout compounds) and retired
all 44 pending group rows through the existing failure transaction. No manual
DB updates or native submissions were performed. The applied-tree focused
suite also passes all 89 tests.
