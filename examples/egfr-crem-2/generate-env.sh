#!/bin/bash
# Shared generate environment for t41 and Slurm calculator nodes.
export PATH=/home1/conda/envs/graft/bin:$PATH
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
