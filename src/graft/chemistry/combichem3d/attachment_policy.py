"""Initial-attachment ancestry, growth budgets and balanced site selection."""


def attachment_labels(query):
    """Legacy labels, also used to explain historical run records."""
    dummies=[a for a in query.GetAtoms() if a.GetAtomicNum()==0]
    labels=[str(a.GetAtomMapNum() if a.HasProp('molAtomMapNumber') else i)
            for i,a in enumerate(dummies)]
    if len(labels)!=len(set(labels)):
        raise ValueError('Initial attachment labels must be distinct')
    return labels


def initial_attachment_spec(query, legacy_limits=None):
    """Decode [*:N] growth budgets with separate stable ordinal root IDs.

    An explicit legacy map or a :0 dummy keeps the old label interpretation.
    This avoids silently changing established :0/:1 artificial projects.
    """
    dummies=[a for a in query.GetAtoms() if a.GetAtomicNum()==0]
    legacy=bool(legacy_limits) or any(a.HasProp('molAtomMapNumber') and a.GetAtomMapNum()==0 for a in dummies)
    if legacy:
        labels=attachment_labels(query)
        limits=dict(legacy_limits or {})
        unknown=set(limits)-set(labels)
        if unknown:
            raise ValueError('Cycle limits reference absent initial attachment labels: '+', '.join(sorted(unknown)))
        mode='legacy_labels'
    else:
        labels=[str(i) for i in range(len(dummies))]
        limits={label:a.GetAtomMapNum() if a.HasProp('molAtomMapNumber') else 999
                for label,a in zip(labels,dummies)}
        if any(n<1 for n in limits.values()):
            raise ValueError('Initial attachment cycle limits must be positive; 999 means unlimited')
        mode='map_cycle_limits'
    return labels,limits,mode


def root_for_site(history, uid):
    for label,anchor in history.get('initial_attachment_labels',{}).items():
        if uid==anchor:
            return label
    atom=next((a for a in history['atoms'] if a['uid']==uid),None)
    if atom is None:
        raise ValueError(f'Unknown attachment atom UID: {uid}')
    root=history.get('branch_roots',{}).get(atom['branch'])
    if root is None:
        raise ValueError(f'Missing initial-attachment ancestry for {uid}')
    return root


def fragment_depths(parent, history):
    """Recover fragment-edge depths from exact saved atom UIDs, never atom distance.

    Each CReM operation attaches one connected fragment with one bond. Collapsing
    those fragments must therefore produce a tree rooted at the retained core.
    Birth iteration and cumulative addition counts do not measure this depth.
    """
    rows={row['uid']:row for row in history['atoms']}
    atoms={a.GetProp('_mn_uid'):a for a in parent.GetAtoms() if a.GetAtomicNum()>1}
    if len(rows)!=len(history['atoms']) or len(atoms)!=parent.GetNumHeavyAtoms() or rows.keys()!=atoms.keys():
        raise ValueError('Fragment depth requires exact saved atom UID provenance')
    core={rows[row['uid']]['fragment'] for row in history['core']}
    if len(core)!=1:
        raise ValueError('Fragment depth requires one retained core fragment')
    graph={row['fragment']:set() for row in rows.values()}
    edges=set()
    for bond in parent.GetBonds():
        a,b=bond.GetBeginAtom(),bond.GetEndAtom()
        if a.GetAtomicNum()<=1 or b.GetAtomicNum()<=1:
            continue
        left,right=(rows[atom.GetProp('_mn_uid')]['fragment'] for atom in (a,b))
        if left!=right:
            edge=frozenset((left,right))
            if edge in edges:
                raise ValueError('Fragment ancestry has multiple attachment bonds')
            edges.add(edge)
            graph[left].add(right);graph[right].add(left)
    depths={next(iter(core)):0}
    pending=list(depths)
    for fragment in pending:
        for neighbor in graph[fragment]:
            if neighbor not in depths:
                depths[neighbor]=depths[fragment]+1
                pending.append(neighbor)
    if len(depths)!=len(graph) or len(edges)!=len(graph)-1:
        raise ValueError('Fragment ancestry must be a connected tree')
    return {uid:depths[row['fragment']] for uid,row in rows.items()}


def growth_available(history, root, *, depth=None):
    limit=history.get('initial_attachment_max_cycles',{}).get(root,999)
    if history.get('growth_mode')=='fixed-frame':
        if depth is None:
            raise ValueError('CReM growth eligibility requires the site fragment depth')
        return limit is None or depth<limit
    return limit==999 or history.get('growth_counts',{}).get(root,0)<limit


def balanced_sites(passing, history, count):
    """Cover active roots first; rotate spare slots by cumulative assignments."""
    groups={}
    for uid,distance in passing:
        root=root_for_site(history,uid)
        if growth_available(history,root):
            groups.setdefault(root,[]).append((uid,distance))
    for sites in groups.values():
        sites.sort(key=lambda item:(-item[1],item[0]))
    used={root:0 for root in groups}
    lifetime=history.get('attachment_selection_counts',{})
    selected=[]
    while len(selected)<count and groups:
        root=min(groups,key=lambda r:(used[r],lifetime.get(r,0),-groups[r][0][1],int(r)))
        selected.append(groups[root].pop(0))
        used[root]+=1
        if not groups[root]:
            del groups[root]
    return selected
