"""Select an exact Glide pose/score pair, optionally preferring point contact."""
import csv
import math
from pathlib import Path
import re
import sys

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


class PoseSelector:
    def __init__(self, point=None):
        self.point = validate_point(point)
        self.scores, self.poses, self.keys = {}, {}, {}
        self.skipped = 0

    def add(self, st, cid):
        try:
            value = st.property['r_i_docking_score']
            score = float(value)
            if isinstance(value, bool) or not math.isfinite(score):
                raise ValueError('Nonfinite Glide score')
        except (KeyError, TypeError, ValueError):
            self.skipped += 1
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
            if distance is not None:
                pose.property['r_graft_attraction_point_distance'] = distance
                pose.property['b_graft_attraction_point_contact'] = distance <= 2.0
            self.keys[cid], self.scores[cid], self.poses[cid] = key, score, pose


def extract(input_file, score_file, pose_file, point=None, structure=None):
    if structure is None:
        from schrodinger import structure
    selected = PoseSelector(point)
    for st in structure.StructureReader(str(input_file)):
        match = re.search(r'CMPID_(\d+)', st.title or '')
        if match:
            selected.add(st, int(match[1]))
    score_file, pose_file = Path(score_file), Path(pose_file)
    pending_pose = pose_file.with_name('best_poses.pending.maegz')
    pending_score = score_file.with_name('docking_scores.pending.tsv')
    with structure.StructureWriter(str(pending_pose)) as writer:
        for cid in sorted(selected.poses):
            writer.append(selected.poses[cid])
    with pending_score.open('w', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['id', 'docking_score'])
        writer.writerows(sorted(selected.scores.items()))
    pending_pose.replace(pose_file)
    pending_score.replace(score_file)
    print(f'Extracted docking scores/poses: {len(selected.scores)}; point preference={selected.point}')
    return selected


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: _extract_glide_scores.py <input.maegz> <scores.tsv> <best_poses.maegz>')
    extract(*sys.argv[1:], point=globals().get('ATTRACTION_POINT'))
