"""Project initialization side effects, invoked after configuration validation."""
from graft import _core


def initialize_project(args) -> None:
    """Preserve migration, reference-cache restore, schema and mkdir ordering."""
    from graft.database import migrate_project_database
    migrate_project_database(
        args.toml_file.parent / f"{args.project}.sqlite", args.db_path
    )
    has_reference_cache = _core.initialize_project_database(
        db_path=args.db_path,
        reference_poses=args.reference_poses,
        schrodinger=args.schrodinger,
        sa_score_cpus=getattr(args, 'sa_score_cpus', 1),
    )
    if has_reference_cache:
        from graft.reference_scores import restore
        restore(args)

    with _core.open_sqlite(args.db_path) as conn:
        _core.ensure_schema_columns(conn)

    args.output.mkdir(parents=True, exist_ok=True)

