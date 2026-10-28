"""``mbnas-core-principal create|rotate|revoke|list`` - owner CLI for scoped principals (WP-2 contract section 1.3,
design A3 section 11.1). Tokens are printed once (JSON on stdout) and only their sha256 is stored.

    mbnas-core-principal create --kind runner --runner-id R1 [--ttl-days 90]
    mbnas-core-principal create --kind server [--ttl-days 365]   # the gateway's service token (WP-3): put it in the
                                                                  # gateway environment; it calls /core/v1/console/*
    mbnas-core-principal create --kind admin [--ttl-days 1]      # owner-local token (admin scope: console + owner)
    mbnas-core-principal rotate <principal_id> [--grace-s 900]
    mbnas-core-principal revoke <principal_id>
    mbnas-core-principal list
"""
from __future__ import annotations

import argparse
import json
import sys

from mbnas_core.config import CoreSettings
from mbnas_core.db.core import connect
from mbnas_core.runner_api import auth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mbnas-core-principal", description="manage core principals (tokens)")
    parser.add_argument("--env-file", default=None)
    sub = parser.add_subparsers(dest="action", required=True)
    c = sub.add_parser("create", help="issue a new token (printed once); --kind server = the gateway's service token "
                                      "for /core/v1/console, --kind admin = owner-local token")
    c.add_argument("--kind", choices=auth.KINDS, required=True)
    c.add_argument("--runner-id", default=None, help="required for --kind runner")
    c.add_argument("--ttl-days", type=float, default=None, help="default: runner 90, server 365, admin 1; 0 = no expiry")
    r = sub.add_parser("rotate", help="issue a replacement token; the old one expires after the grace period")
    r.add_argument("principal_id")
    r.add_argument("--grace-s", type=float, default=auth.ROTATION_GRACE_S)
    r.add_argument("--ttl-days", type=float, default=None)
    v = sub.add_parser("revoke", help="revoke a token now")
    v.add_argument("principal_id")
    sub.add_parser("list", help="list principals (never the token)")
    args = parser.parse_args(argv)
    settings = CoreSettings.load(args.env_file)
    conn = connect(settings)
    try:
        if args.action == "create":
            ttl = None if args.ttl_days is None else args.ttl_days * 86400
            try:
                row, token = auth.create_principal(conn, args.kind, runner_id=args.runner_id, ttl_s=ttl)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(json.dumps({"principal": row, "token": token}))
        elif args.action == "rotate":
            ttl = None if args.ttl_days is None else args.ttl_days * 86400
            try:
                row, token = auth.rotate_principal(conn, args.principal_id, grace_s=args.grace_s, ttl_s=ttl)
            except LookupError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(json.dumps({"principal": row, "token": token}))
        elif args.action == "revoke":
            ok = auth.revoke_principal(conn, args.principal_id)
            print(json.dumps({"principal_id": args.principal_id, "revoked": ok}))
            return 0 if ok else 2
        else:
            print(json.dumps({"principals": auth.list_principals(conn)}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
