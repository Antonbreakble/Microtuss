"""Command-line front end.

    python -m iecst run FB_Motor.st --pou FB_Motor --set Start=true --scans 60
    python -m iecst check FB_Motor.st
    python -m iecst scan FB_Motor.st --set Start=true --scans 5 --trace

The commands are thin: they parse arguments, call :mod:`iecst.core.session`,
and print.  Anything with a decision in it lives in the core, so the shell and
a future GUI cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from iecst.core.errors import IecError
from iecst.core.session import Session, check, load
from iecst.testing.bench import Testbench

__all__ = ["build_parser", "main"]


def _parse_value(text: str) -> object:
    """Interpret a command-line value with PLC-friendly spellings.

    A STRUCT or ARRAY can be given as JSON, which is the only way to supply a
    DUT from the shell::

        --set 'Cfg={"mode": "Automatic", "ramptime": "200ms"}'

    Anything else stays a string, and the runtime decodes it (``"1s"`` for a
    TIME, ``"Automatic"`` for an enumeration member).
    """
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except ValueError:
            pass  # not JSON after all -- treat it as a plain string
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    for caster in (int, float):
        try:
            return caster(text)
        except ValueError:
            pass
    return text


def _pairs(items: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"error: expected NAME=VALUE, got '{item}'")
        out[key] = _parse_value(value)
    return out


def _source_files(args: argparse.Namespace) -> list[str | Path]:
    """The files to load for one command: the libraries first, then the POU."""
    return list(args.include or []) + [args.source]


def cmd_run(args: argparse.Namespace) -> int:
    if args.expect:
        tb = Testbench.from_file(
            args.source, args.pou, cycle_time=args.cycle, includes=args.include
        )
        tb.set_many(_pairs(args.set))
        tb.run(args.scans)
        tb.expect(**_pairs(args.expect))
        print(tb.summary())
        return 1 if tb.failures else 0

    session = Session.from_files(_source_files(args), args.pou, cycle_time=args.cycle)
    session.set_many(_pairs(args.set))
    session.run(args.scans)
    print(json.dumps(session.snapshot(), indent=2, default=str))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    session = Session.from_files(_source_files(args), args.pou, cycle_time=args.cycle)
    session.set_many(_pairs(args.set))
    for _ in range(args.scans):
        session.step()
        if args.trace:
            print(
                f"scan {session.scans} t={session.now}: "
                f"{json.dumps(session.snapshot(), default=str)}"
            )
    print(json.dumps(session.snapshot(), indent=2, default=str))
    return 0


def cmd_vars(args: argparse.Namespace) -> int:
    """List the POU's interface -- what ``--set`` accepts and what comes out."""
    session = Session.from_files(_source_files(args), args.pou, cycle_time=args.cycle)
    print(f"{session.pou_name} ({session.pou_kind})")
    for label, variables in (("inputs", session.inputs()), ("outputs", session.outputs())):
        if not variables:
            continue
        print(f"  {label}:")
        for variable in variables:
            members = variable.enum_members
            detail = f"  ({', '.join(members)})" if members else ""
            print(f"    {variable.name:<20} {variable.type_name}{detail}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    sources: list[str | Path] = list(args.source)
    sources.extend(args.include or [])
    # check() reports a syntax error as a problem rather than raising, so this
    # command says what is wrong without a try/except of its own.
    if problems := check(sources):
        for problem in problems:
            print(problem.format(), file=sys.stderr)
        return 1

    model = load(sources)
    print(f"ok: {len(model.pous)} POU(s), {len(model.types)} type(s), {len(model.globals)} global(s)")
    for entry in sorted(model.pous.values(), key=lambda e: e.name):
        print(f"  {entry.kind:<14} {entry.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iecst", description="Run and test IEC 61131-3 Structured Text function blocks."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("source", help="the .st file containing the FB under test")
        p.add_argument("--pou", help="name of the POU to run (default: the only one found)")
        p.add_argument("--include", action="append", default=[],
                       help="extra .st file to load first (library); repeatable")
        p.add_argument("--cycle", default="10ms", help="scan cycle time, e.g. 10ms, 1s")
        p.add_argument("--set", action="append", default=[],
                       help="set an input before running, NAME=VALUE; repeatable")

    run_p = sub.add_parser("run", help="run the POU and print the resulting variables")
    common(run_p)
    run_p.add_argument("--scans", type=int, default=1, help="number of scans to run")
    run_p.add_argument("--expect", action="append", default=[],
                       help="assert NAME=VALUE after the run; repeatable")
    run_p.set_defaults(func=cmd_run)

    scan_p = sub.add_parser("scan", help="print the variable state after every scan")
    common(scan_p)
    scan_p.add_argument("--scans", type=int, default=1)
    scan_p.add_argument("--trace", action="store_true", help="print each scan, not just the last")
    scan_p.set_defaults(func=cmd_scan)

    vars_p = sub.add_parser("vars", help="list the POU's inputs and outputs")
    common(vars_p)
    vars_p.set_defaults(func=cmd_vars)

    check_p = sub.add_parser("check", help="parse and link without executing")
    check_p.add_argument("source", nargs="+", help="one or more .st files")
    check_p.add_argument("--include", action="append", default=[])
    check_p.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except IecError as exc:
        print(exc.format(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
