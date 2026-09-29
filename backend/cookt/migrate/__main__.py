"""CLI: uv run --group dev python -m cookt.migrate {run,verify,report}.

run     build a fresh DB from data/sources snapshots into a temp file, verify, then atomically
        replace --out (refuses to clobber non-migration data unless --force)
verify  re-run the verification checks against --out (exit 1 on any failure)
report  re-render docs/migration-report.md from data/migration/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import settings
from .build import MigrationError, Paths, run


def _print_checks(checks: list[dict]) -> None:
    for check in checks:
        detail = check["detail"]
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        print(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cookt.migrate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="build + verify + replace the DB")
    p_run.add_argument("--out", type=Path, default=settings.db_path)
    p_run.add_argument(
        "--force",
        action="store_true",
        help="replace even if the DB holds non-migration data (old copy kept)",
    )
    p_verify = sub.add_parser("verify", help="re-run verification only")
    p_verify.add_argument("--out", type=Path, default=settings.db_path)
    sub.add_parser("report", help="re-render docs/migration-report.md from report.json")
    args = parser.parse_args(argv)
    paths = Paths(settings.data_dir)

    if args.command == "run":
        try:
            report = run(args.out, force=args.force)
        except MigrationError as exc:
            print(f"migration FAILED: {exc}", file=sys.stderr)
            return 1
        o = report["outcomes"]
        print(f"A: {o['A']}\nB: {o['B']}\nrecipes: {report['recipes']}")
        print(
            f"matches: {report['matching']['pairs_by_rule']}, review candidates: "
            f"{report['matching']['review_candidates']}"
        )
        print(f"verification: {'PASS' if report['verification']['ok'] else 'FAIL'}")
        _print_checks(report["verification"]["checks"])
        print(f"report: {paths.report_json} + {paths.report_md} ({report['duration_seconds']}s)")
        return 0

    if args.command == "verify":
        from . import report as R
        from .verify import verify

        out = args.out.resolve()
        if not out.exists():
            print(f"{out} does not exist", file=sys.stderr)
            return 1
        result = verify(out, out.parent / "images", paths)
        print(f"verification: {'PASS' if result['ok'] else 'FAIL'}")
        _print_checks(result["checks"])
        if paths.report_json.exists():
            report = json.loads(paths.report_json.read_text())
            report["verification"] = result
            R.write(report, paths)
        return 0 if result["ok"] else 1

    if args.command == "report":
        from . import report as R

        if not paths.report_json.exists():
            print(f"{paths.report_json} missing; run the migration first", file=sys.stderr)
            return 1
        report = json.loads(paths.report_json.read_text())
        paths.report_md.write_text(R.render_markdown(report))
        print(paths.report_md)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
