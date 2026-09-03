from __future__ import annotations

import argparse
import getpass
from datetime import date

from outset_ready.connectors.garmin.client import GarminConnectorError
from outset_ready.connectors.garmin.config import load_garmin_settings
from outset_ready.connectors.garmin.sync import sync_garmin


def prompt_mfa() -> str:
    return getpass.getpass("Garmin MFA code: ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outset-ready")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync-garmin",
        help="Fetch Garmin data into the local Ready database.",
    )
    sync_parser.add_argument("--days", type=_positive_int, default=7)
    sync_parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    sync_parser.add_argument("--activity-page-size", type=_positive_int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "sync-garmin":
        return 2

    settings = load_garmin_settings()
    try:
        stats = sync_garmin(
            settings,
            days=args.days,
            end_date=args.end_date,
            activity_page_size=args.activity_page_size,
            prompt_mfa=prompt_mfa,
        )
    except GarminConnectorError as exc:
        print(f"Garmin sync failed: {exc}")
        return 1

    print(f"Garmin sync: {stats.start_date} to {stats.end_date}")
    print(f"Daily records: {stats.daily_records}")
    print(f"Activities: {stats.activity_records} of {stats.activities_fetched} fetched")
    print(f"Raw payloads saved: {stats.payloads_saved}")
    print(f"Warnings: {len(stats.warnings)}")
    for warning in stats.warnings:
        print(f"- {warning}")
    return 0


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


if __name__ == "__main__":
    raise SystemExit(main())

