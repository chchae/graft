"""Select an exact Glide pose/score pair, optionally preferring point contact."""
import csv
import json
import math
from pathlib import Path
import re
import sys
import uuid

CONTACT_POSES_PER_LIG = 10


class PoseGeometryError(ValueError):
    pass


def validate_point(point):
    if point is None:
        return None
    if (not isinstance(point, (list, tuple)) or len(point) != 3
            or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in point)):
        raise ValueError('Glide attraction point requires three finite coordinates')
    return tuple(float(x) for x in point)


class CoreValidator:
    """Check all core matches in the receptor frame; never align a pose."""
    def __init__(self, reference, smarts, atoms, tolerance=1.0, *, matcher=None):
        if matcher is None:
            from schrodinger import adapter
            matcher = adapter.evaluate_smarts
        self.matcher, self.smarts = matcher, smarts
        self.atoms = list(atoms)
        self.tolerance = float(tolerance)
        if (not self.atoms or len(set(self.atoms)) != len(self.atoms)
                or any(type(i) is not int or i < 1 for i in self.atoms)
                or not math.isfinite(self.tolerance) or not 0 < self.tolerance <= 1.0):
            raise PoseGeometryError('Invalid frozen Glide core mapping/tolerance')
        matches = matcher(reference, smarts, False)
        if self.atoms not in [list(m) for m in matches]:
            raise PoseGeometryError('Frozen reference core atom mapping does not match SMARTS')
        self.xyz = self.coordinates(reference, self.atoms)

    @staticmethod
    def coordinates(st, atoms):
        try:
            xyz = []
            for i in atoms:
                atom = st.atom[i]
                if atom.atomic_number <= 1:
                    raise ValueError('Core must identify heavy atoms')
                point = (atom.x, atom.y, atom.z)
                if not all(math.isfinite(x) for x in point):
                    raise ValueError('Nonfinite core coordinates')
                xyz.append(point)
            return xyz
        except (IndexError, KeyError, AttributeError, TypeError, ValueError) as exc:
            raise PoseGeometryError('Invalid Glide core coordinates/mapping') from exc

    def matches(self, st):
        try:
            return self.matcher(st, self.smarts, False)
        except Exception as exc:
            raise PoseGeometryError('Cannot verify Glide core match') from exc

    def rmsd(self, st):
        values = []
        for match in self.matches(st):
            if len(match) != len(self.xyz):
                raise PoseGeometryError('Glide core match has incorrect atom count')
            xyz = self.coordinates(st, match)
            value = math.sqrt(sum(sum((a-b)**2 for a,b in zip(p,q))
                                  for p,q in zip(xyz,self.xyz))/len(xyz))
            if not math.isfinite(value):
                raise PoseGeometryError('Nonfinite Glide core RMSD')
            values.append(value)
        return min(values) if values else None

    @classmethod
    def from_directory(cls, directory):
        inputs = sorted(Path(directory).glob('*.in'))
        if len(inputs) != 1:
            raise PoseGeometryError('Missing or ambiguous saved Glide input; core validation required')
        return cls.from_input(inputs[0])

    @classmethod
    def from_input(cls, path, *, structure=None, matcher=None):
        path = Path(path)
        fields = {}
        for line in path.read_text().splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not (parts[0].startswith('CORE_') or parts[0] == 'REF_LIGAND_FILE'):
                continue
            if parts[0] in fields:
                raise PoseGeometryError('Repeated frozen Glide core keyword')
            fields[parts[0]] = parts[1]
        if fields.get('CORE_DEFINITION') != 'smarts' or fields.get('CORE_RESTRAIN','').lower() not in ('true','yes'):
            raise PoseGeometryError('Explicit restrained SMARTS core required for result validation')
        try:
            smarts = fields['CORE_SMARTS'].strip('"')
            atoms = [int(i.strip()) for i in fields['CORE_ATOMS'].split(',')]
            tolerance = float(fields['CORE_POS_MAX_RMSD'])
            reference = Path(fields['REF_LIGAND_FILE'].strip('"'))
        except (KeyError, ValueError) as exc:
            raise PoseGeometryError('Incomplete frozen Glide core input') from exc
        if not reference.is_absolute():
            reference = path.parent/reference
        if structure is None:
            from schrodinger import structure
        structures = list(structure.StructureReader(str(reference)))
        if len(structures) != 1:
            raise PoseGeometryError('Glide core reference must contain exactly one structure')
        return cls(structures[0], smarts, atoms, tolerance, matcher=matcher)


def archive_rejected_pose(path):
    """Retain previous output as audit, outside the best-pose publication paths."""
    if path.exists():
        path.replace(path.with_name(path.stem+'.rejected-'+uuid.uuid4().hex+path.suffix))


def write_core_report(path, selected):
    report = dict(policy='fixed_reference_core_v1', tolerance=selected.core.tolerance,
                  smarts=selected.core.smarts, reference_atoms=selected.core.atoms,
                  accepted_ids=sorted(selected.poses), rejected=selected.core_rejections)
    pending = path.with_name(path.name+'.pending')
    pending.write_text(json.dumps(report, indent=2)+'\n')
    pending.replace(path)


class PoseSelector:
    def __init__(self, point=None, *, core=None):
        self.point = validate_point(point)
        self.scores, self.poses, self.keys = {}, {}, {}
        self.skipped = 0
        self.core = core
        self.core_rejections = {}

    def add(self, st, cid):
        try:
            value = st.property['r_i_docking_score']
            score = float(value)
            if isinstance(value, bool) or not math.isfinite(score):
                raise ValueError('Nonfinite Glide score')
        except (KeyError, TypeError, ValueError):
            self.skipped += 1
            return
        core_rmsd = None
        if self.core is not None:
            core_rmsd = self.core.rmsd(st)
            reason = ('no_core_match' if core_rmsd is None else
                      'core_rmsd_exceeded' if core_rmsd > self.core.tolerance else None)
            if reason:
                reasons = self.core_rejections.setdefault(cid, {})
                reasons[reason] = reasons.get(reason, 0) + 1
                return
        distance = None
        if self.point is not None:
            distances = []
            try:
                for atom in st.atom:
                    if atom.atomic_number <= 1:
                        continue
                    xyz = (atom.x, atom.y, atom.z)
                    if not all(math.isfinite(x) for x in xyz):
                        raise ValueError('Nonfinite coordinates')
                    # Match the downstream attraction-point measurement exactly.
                    distances.append(math.sqrt(sum((x-y)**2 for x,y in zip(xyz,self.point))))
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise PoseGeometryError(f'Invalid ligand coordinates in CMPID_{cid}') from exc
            if not distances or not math.isfinite(min(distances)):
                raise PoseGeometryError(f'No finite ligand heavy-atom distance in CMPID_{cid}')
            distance = min(distances)
        key = (distance > 2.0, score) if distance is not None else (False, score)
        if cid not in self.keys or key < self.keys[cid]:
            pose = st.copy()
            pose.property['i_user_compound_id'] = cid
            if core_rmsd is not None:
                pose.property['r_graft_core_rmsd'] = core_rmsd
            if distance is not None:
                pose.property['r_graft_attraction_point_distance'] = distance
                pose.property['b_graft_attraction_point_contact'] = distance <= 2.0
            self.keys[cid], self.scores[cid], self.poses[cid] = key, score, pose


def extract(input_file, score_file, pose_file, point=None, structure=None, *, core=None):
    if structure is None:
        from schrodinger import structure
    selected = PoseSelector(point, core=core)
    for st in structure.StructureReader(str(input_file)):
        match = re.search(r'CMPID_(\d+)', st.title or '')
        if match:
            selected.add(st, int(match[1]))
    score_file, pose_file = Path(score_file), Path(pose_file)
    pending_pose = pose_file.with_name('best_poses.pending.maegz')
    pending_score = score_file.with_name('docking_scores.pending.tsv')
    if selected.poses:
        with structure.StructureWriter(str(pending_pose)) as writer:
            for cid in sorted(selected.poses):
                writer.append(selected.poses[cid])
    with pending_score.open('w', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['id', 'docking_score'])
        writer.writerows(sorted(selected.scores.items()))
    if selected.poses:
        pending_pose.replace(pose_file)
    else:
        archive_rejected_pose(pose_file)
    if core is not None:
        write_core_report(pose_file.parent/'core_validation.json', selected)
    pending_score.replace(score_file)
    print(f'Extracted docking scores/poses: {len(selected.scores)}; point preference={selected.point}')
    return selected


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: _extract_glide_scores.py <input.maegz> <scores.tsv> <best_poses.maegz>')
    core = (CoreValidator.from_directory(Path(sys.argv[3]).parent)
            if globals().get('REQUIRE_CORE_VALIDATION', True) else None)
    extract(*sys.argv[1:], point=globals().get('ATTRACTION_POINT'), core=core)
