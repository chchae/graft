# Testing GRAFT

Run from the repository root with Python >=3.11, RDKit and pytest:

```bash
python -m pip install -e .
python -m pytest
python -m compileall -q src tests
python -c "import graft; import graft.cli"
```

The current working-tree baseline is **2,882 passed, zero failed, seven skipped**.
The seven launcher cases need both Torch and the external REINVENT4 LibInvent
package. They are not scientific end-to-end tests. In an existing environment
providing those packages, run:

```bash
python -m pytest -ra tests/test_reinvent_launcher.py
```

That environment was unavailable during the refinement; installation, GPU/native
calculations and new dependency support were not assumed. A skip remains visible.
The verified full-suite environment used Python 3.15.2/RDKit 2026.09.1; the declared
>=3.11 compatibility is not a claim that every version was tested.

## Isolation

`tests/conftest.py` creates a private session directory for fallback submission
locks and the MD registry before collection, retains it through teardown, and
restores its patches at cleanup. Function fixtures retain their existing private
lock and registry paths. Exec children inherit a private TMPDIR. Multiprocessing allocates its separately finalized socket directory before the
pytest tempfile cache is patched. The session cache is restored during cleanup;
multiprocessing retains ownership of its own directory through interpreter exit. No external pytest plugin, custom environment
variable or fixed /tmp script is needed.

The isolation test runs an ordinary nested pytest process and checks collection,
function fixtures, session teardown, child TMPDIR, multiprocessing temporary-file
ownership and clean process exit. It does not release/delete a production lock
or inspect native JobIds. Test fixtures must continue to mock external submission.
This is not a sandbox for arbitrary integration tests.

## Expectations and failures

The two old MD summary/timing tests now explicitly select verbosity 2 and restore
the ContextVar afterwards. Existing quiet/error policy tests still check level 1.
The self-clash regression freezes an artificial colliding geometry and tests the
real search/clash classification, keeping CLASH_REJECT and provenance assertions.
A free optimizer is no longer assumed to leave that geometry colliding. This does
not change clash thresholds or scientific code.

The /proc exit probe reads once and accepts a vanished process or terminal Z/X.
A running/sleeping process still fails; other I/O errors still propagate. A real
Python parent/child timeout test retains the inherited-lock check without running
Amber.

There are no xfail/skip/assertion relaxations hiding the old three failures. See
[the refinement report](refactoring/REFINEMENT_REPORT.md) for the intentional
fixture/precondition repairs and before/after node comparison.
