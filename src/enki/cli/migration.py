"""Migration commands: migrate, validate, rollback."""

import sys


def cmd_migrate(args):
    """Migrate from Odin/Freyja to Enki."""
    from ..migration import migrate_to_enki

    print("Starting migration to Enki...")
    print()
    result = migrate_to_enki(
        generate_embeddings=not args.skip_embeddings,
        archive_hooks=not args.skip_hooks,
        install_hooks=not args.skip_hooks,
    )
    print("Migration complete!")
    print()
    print(f"  Beads migrated: {result.beads_migrated}")
    print(f"  Sessions migrated: {result.sessions_migrated}")
    print(f"  Projects migrated: {result.projects_migrated}")
    print(f"  Embeddings generated: {result.embeddings_generated}")
    print(f"  Hooks archived: {result.hooks_archived}")
    print(f"  Hooks installed: {result.hooks_installed}")
    if result.errors:
        print()
        print("Errors encountered:")
        for error in result.errors:
            print(f"  - {error}")
    print()
    print("Run 'enki migrate --validate' to verify migration.")


def cmd_migrate_validate(args):
    """Validate migration."""
    from ..migration import validate_migration

    print("Validating migration...")
    print()
    checks = validate_migration()
    all_passed = True

    status = "PASS" if checks["enki_db_exists"] else "FAIL"
    if status == "FAIL":
        all_passed = False
    print(f"  [{status}] Enki database exists")
    print(f"  [INFO] Beads migrated: {checks['beads_count']}")
    print(f"  [INFO] Embeddings generated: {checks['embeddings_count']}")

    if checks["beads_without_embeddings"] > 0:
        print(f"  [WARN] Beads without embeddings: {checks['beads_without_embeddings']}")
    else:
        print(f"  [PASS] All beads have embeddings")

    status = "PASS" if checks["odin_hooks_archived"] else "FAIL"
    if status == "FAIL":
        all_passed = False
    print(f"  [{status}] Odin hooks archived")

    status = "PASS" if checks["freyja_hooks_archived"] else "FAIL"
    if status == "FAIL":
        all_passed = False
    print(f"  [{status}] Freyja hooks archived")

    if checks["enki_hooks_installed"] >= 2:
        print(f"  [PASS] Enki hooks installed: {checks['enki_hooks_installed']}")
    else:
        print(f"  [WARN] Enki hooks installed: {checks['enki_hooks_installed']}")

    if checks["errors"]:
        print()
        print("Errors:")
        for error in checks["errors"]:
            print(f"  - {error}")
            all_passed = False

    print()
    if all_passed:
        print("Migration validation PASSED")
    else:
        print("Migration validation FAILED - see errors above")
        sys.exit(1)


def cmd_migrate_rollback(args):
    """Rollback migration."""
    from ..migration import rollback_migration

    if not args.force:
        print("This will restore old Odin/Freyja hooks and remove Enki hooks.")
        print("Migrated beads will NOT be deleted.")
        print()
        print("Run with --force to confirm.")
        sys.exit(1)
    print("Rolling back migration...")
    rollback_migration()
    print("Rollback complete.")
    print("Note: Migrated beads have been preserved in Enki database.")


def register(subparsers):
    """Register migration commands."""
    p = subparsers.add_parser("migrate", help="Migrate from Odin/Freyja to Enki")
    p.add_argument("--skip-embeddings", action="store_true",
        help="Skip embedding generation (faster but no semantic search)")
    p.add_argument("--skip-hooks", action="store_true",
        help="Don't archive old hooks or install new ones")
    p.add_argument("--validate", action="store_true",
        help="Validate migration instead of running it")
    p.add_argument("--rollback", action="store_true",
        help="Rollback migration (restore old hooks)")
    p.add_argument("--force", action="store_true",
        help="Force operation (required for rollback)")
    p.set_defaults(func=lambda args:
        cmd_migrate_validate(args) if args.validate else
        cmd_migrate_rollback(args) if args.rollback else
        cmd_migrate(args))
