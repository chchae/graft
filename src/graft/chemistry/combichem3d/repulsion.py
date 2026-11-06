"""Optional fixed-receptor steric penalty; not a binding-energy model."""
import numpy as np
from scipy.optimize import minimize as scipy_minimize
from scipy.spatial import cKDTree

from graft.chemistry.combichem3d.clashes import atom_data


class ReceptorRepulsion:
    """E = k/2 * sum(max(0, R_lig + R_rec - distance)**2).

    Use the checker's exact receptor/atom policy, but penalize all vdW overlap,
    leaving clearance before the final checker's overlap-tolerance boundary.
    Neighbours are recomputed at every evaluation, including line searches.
    """
    def __init__(self, ligand, checker, strength):
        self.indices, _, self.radii = atom_data(ligand, checker.cfg['include-hydrogens'])
        self.xyz = np.array(checker.xyz, copy=True)
        self.receptor_radii = np.array(checker.radii, copy=True)
        self.tree = cKDTree(self.xyz)
        self.cutoffs = self.radii + checker.max_radius
        self.strength = strength

    def energy_gradient(self, coordinates):
        gradient = np.zeros_like(coordinates)
        energy = 0.
        points = coordinates[self.indices]
        neighbours = self.tree.query_ball_point(points, self.cutoffs)
        for index, radius, point, near in zip(self.indices, self.radii, points, neighbours):
            if not near:
                continue
            delta = point - self.xyz[near]
            # Smooth the norm at coincident coordinates; keep finite gradients.
            distance = np.sqrt(np.sum(delta * delta, axis=1) + 1e-12)
            overlap = np.maximum(0., radius + self.receptor_radii[near] - distance)
            energy += .5 * self.strength * float(overlap @ overlap)
            gradient[index] = -self.strength * np.sum((overlap / distance)[:, None] * delta, axis=0)
        return energy, gradient


def minimize(mol, force_field, kind, fixed, checker, cfg):
    """Minimize ligand FF + steric penalty with fixed atoms removed from variables."""
    coordinates = np.array(mol.GetConformer().GetPositions(), copy=True)
    movable = np.array([a.GetIdx() for a in mol.GetAtoms()
                        if not (a.HasProp('_mn_uid') and a.GetProp('_mn_uid') in fixed)], dtype=int)
    penalty = ReceptorRepulsion(mol, checker, cfg['receptor-repulsion-strength'])

    def objective(values):
        positions = coordinates.copy()
        positions[movable] = values.reshape((-1, 3))
        flat = positions.ravel().tolist()
        ligand_energy = force_field.CalcEnergy(flat)
        ligand_gradient = np.asarray(force_field.CalcGrad(flat)).reshape((-1, 3))
        receptor_energy, receptor_gradient = penalty.energy_gradient(positions)
        energy = ligand_energy + receptor_energy
        gradient = (ligand_gradient + receptor_gradient)[movable].ravel()
        if not np.isfinite(energy) or not np.isfinite(gradient).all():
            raise ValueError('Nonfinite receptor-aware optimization energy/gradient')
        return energy, gradient

    if len(movable):
        result = scipy_minimize(objective, coordinates[movable].ravel(), jac=True, method='L-BFGS-B',
                                options={'maxiter': cfg['optimization-iterations'], 'maxls': 50,
                                         'maxfun': 1000000, 'ftol': 1e-9, 'gtol': 1e-4})
        if not result.success and result.status != 1:
            raise ValueError(f'Receptor-aware optimization failed: {result.message}')
        coordinates[movable] = result.x.reshape((-1, 3))
        converged = bool(result.success)
        iterations = int(result.nit)
    else:
        converged, iterations = True, 0
    total, _ = objective(coordinates[movable].ravel())
    ligand_energy = float(force_field.CalcEnergy(coordinates.ravel().tolist()))
    receptor_energy, _ = penalty.energy_gradient(coordinates)
    for index, xyz in enumerate(coordinates):
        mol.GetConformer().SetAtomPosition(index, xyz)
    return kind, ligand_energy, dict(
        converged=converged, return_code=0 if converged else 1,
        status='converged' if converged else 'iteration_limit',
        max_iterations=cfg['optimization-iterations'], iterations=iterations,
        objective='ligand_ff_plus_fixed_receptor_repulsion_v1',
        receptor_repulsion_strength=cfg['receptor-repulsion-strength'],
        ligand_energy=ligand_energy, receptor_repulsion_energy=float(receptor_energy), total_energy=float(total))
