from __future__ import annotations

import argparse
import getpass
from datetime import date
from pathlib import Path

from outset_ready.auth import hash_password
from outset_ready.connectors.garmin.client import GarminConnectorError
from outset_ready.connectors.garmin.config import load_garmin_settings
from outset_ready.connectors.garmin.sync import sync_garmin
from outset_ready.connectors.garmin.tokens import write_token_bundle
from outset_ready.credentials import generate_credential_encryption_key


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
    subparsers.add_parser(
        "hash-password",
        help="Generate the owner password hash for application settings.",
    )
    export_parser = subparsers.add_parser(
        "export-garmin-token",
        help="Authenticate locally and export a token file for hosted Ready.",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/garmin-token.json"),
    )
    subparsers.add_parser(
        "generate-encryption-key",
        help="Generate the application credential encryption key.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hash-password":
        password = getpass.getpass("Owner password: ")
        confirmation = getpass.getpass("Confirm owner password: ")
        if password != confirmation:
            print("Passwords did not match.")
            return 1
        try:
            print(hash_password(password))
        except ValueError as exc:
            print(exc)
            return 1
        return 0

    if args.command == "generate-encryption-key":
        print(generate_credential_encryption_key())
        return 0

    if args.command == "export-garmin-token":
        settings = load_garmin_settings()
        from outset_ready.connectors.garmin.client import GarminClient

        client = GarminClient(settings)
        try:
            client.login(prompt_mfa=prompt_mfa)
            output_path = write_token_bundle(args.output, client.export_token_bundle())
        except (GarminConnectorError, ValueError, OSError) as exc:
            print(f"Garmin token export failed: {exc}")
            return 1
        print(f"Garmin token written to {output_path}")
        print("Upload this file through Ready, then remove the local export.")
        return 0

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
