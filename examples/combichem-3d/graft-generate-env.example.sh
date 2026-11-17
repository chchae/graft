#!/bin/bash
# Shared Python environment used by both the invoking coordinator and Slurm nodes.
# Adjust this absolute path if your installation differs.
export PATH=/home1/conda/envs/graft/bin:$PATH
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
# Graft's Slurm launcher supplies the shared source tree through PYTHONPATH.
