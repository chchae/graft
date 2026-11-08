"""Drug-likeness priority and historical preselection compatibility."""


def filter_druglike(rows):
    """Historical legacy policy: keep RO5<=1 and the best QED/SA half."""
    target_count = (len(rows) + 1) // 2
    candidates = [i for i, row in enumerate(rows) if row['ro5_violations'] <= 1]
    candidates.sort(key=lambda i: (-rows[i]['qed'], rows[i]['sa_score']))
    selected = set(candidates[:target_count])
    return [
        {**row, 'druglike_selected': i in selected,
         'druglike_reject_reason': ('ro5_violations' if row['ro5_violations'] > 1
                                   else 'qed_rank' if i not in selected else None)}
        for i, row in enumerate(rows)
    ]


def rank_druglike(rows):
    """Rank every molecule by fewer RO5 violations, higher QED, then lower SA.

    Input order breaks full ties (database populations are ordered by ID).
    No molecule is excluded, even when every molecule violates RO5.
    """
    ordered = sorted(range(len(rows)), key=lambda i: (
        rows[i]['ro5_violations'], -rows[i]['qed'], rows[i]['sa_score']))
    ranks = {index: rank for rank, index in enumerate(ordered, 1)}
    return [{**row, 'druglike_selected': True, 'druglike_reject_reason': None,
             'druglike_priority': ranks[i]} for i, row in enumerate(rows)]
