"""Export exact CMPID best Glide poses through installed native structure I/O."""
import json
import math
from pathlib import Path
import re
import sys


def export(request):
    from schrodinger import structure
    wanted = {int(k): v for k, v in request['scores'].items()}
    found = {}
    with structure.StructureReader(request['source']) as reader:
        for st in reader:
            match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
            if not match or int(match[1]) not in wanted:
                continue
            cid = int(match[1])
            score = st.property.get('r_i_docking_score')
            if cid in found or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError('Duplicate or unscored elite docking pose')
            if not math.isclose(score, wanted[cid], abs_tol=1e-5, rel_tol=0):
                raise ValueError('Elite pose differs from its saved best Glide score')
            found[cid] = st
    if set(found) != set(wanted):
        raise ValueError('Missing selected elite docking poses')
    target = Path(request['output'])
    pending = target.with_name(target.stem + '.pending.sdf')
    with structure.StructureWriter(str(pending)) as writer:
        for cid in sorted(found):
            writer.append(found[cid])
    pending.replace(target)


if __name__ == '__main__':
    export(json.loads(Path(sys.argv[1]).read_text()))
