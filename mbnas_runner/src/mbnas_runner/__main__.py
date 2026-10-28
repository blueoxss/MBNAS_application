"""``python -m mbnas_runner`` / ``mbnas-runner`` entry point.

* ``mbnas-runner run [--once] [--max-jobs N] [--env-file F]`` - the runner daemon (:mod:`mbnas_runner.daemon`):
  register with the core, replay the journal, claim -> start -> heartbeat -> progress -> result -> ack;
* ``mbnas-runner register [--env-file F]`` - register once and print the negotiated configuration (no job runs);
* ``mbnas-runner --spec-file JSON`` - run one job from a JobSpec file, exactly what ``python -m mbnas_runner.child``
  does.

A ``core_url`` that is plain ``http://`` to a non-loopback host is refused before anything starts (exit 2, code
``CORE_URL_INSECURE``). The guard matters: an import walk (``pkgutil.walk_packages``) imports this module as
``mbnas_runner.__main__``, and an unguarded ``main()`` call would start work on import.
Heavy modules (the daemon, httpx, torch) are imported only inside the chosen command.

``run`` and ``register`` install the shared log redaction before anything talks to the core
(:func:`install_redaction`): no ``token``/``access_token``/``sig``/``exp`` query value and no ``Authorization`` value in
a log record of the runner process. The daemon's own progress lines are written without secrets (job ids and error
codes only).
"""
from __future__ import annotations

import argparse
import json
import sys

# loggers of the runner process: the HTTP client (request lines with URLs), the runner package and the root logger
REDACTED_LOGGERS = ("httpx", "httpcore", "mbnas_runner", "")


def install_redaction() -> None:
    """Run :func:`mbnas_contracts.util.log_redact.install_log_redaction` on :data:`REDACTED_LOGGERS` (filters on
    those loggers and their current handlers), plus the same filter on :data:`logging.lastResort`, which writes the
    records of every logger when no handler is configured (the runner configures none; a filter on a logger does not
    see the records its child loggers propagate). Idempotent; the filter never drops a record."""
    import logging

    from mbnas_contracts.util.log_redact import RedactSecretsFilter, install_log_redaction

    install_log_redaction(REDACTED_LOGGERS)
    last = logging.lastResort
    if last is not None and not any(isinstance(f, RedactSecretsFilter) for f in last.filters):
        last.addFilter(RedactSecretsFilter())


def _settings(env_file: str | None):  # noqa: ANN202 - RunnerSettings, imported lazily
    from mbnas_runner.config import RunnerSettings

    return RunnerSettings.load(env_file)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mbnas-runner", description="MBNAS runner: executes training, evaluation and benchmark jobs leased from "
                                         "the core (/core/v1/runner) or one job from a JobSpec file")
    parser.add_argument("--spec-file", metavar="JSON", help="run the job described by this JobSpec JSON file; JSONL "
                        "progress lines go to stdout, the last line is the result (exit 0 = SUCCEEDED, 3 = otherwise)")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run the runner daemon (claim -> start -> heartbeat -> result -> ack)")
    run.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    run.add_argument("--max-jobs", type=int, default=None, help="exit after this many jobs")
    run.add_argument("--env-file", default=None, help="dotenv file with MBNAS_RUNNER_* settings")
    reg = sub.add_parser("register", help="register with the core once and print the negotiated configuration")
    reg.add_argument("--env-file", default=None, help="dotenv file with MBNAS_RUNNER_* settings")
    args = parser.parse_args(argv)
    if args.spec_file is not None:
        if args.command is not None:
            parser.error("--spec-file cannot be combined with a command")
        from mbnas_runner.child import main as child_main

        return child_main(["--spec-file", args.spec_file])
    if args.command is None:
        parser.error("a command (run | register) or --spec-file is required")
    install_redaction()  # before the settings, the client or the daemon exist
    from mbnas_runner.client import CoreError, CoreUnavailable, RunnerConfigError

    try:
        settings = _settings(args.env_file)
        if args.command == "register":
            from mbnas_runner.daemon import RunnerDaemon

            daemon = RunnerDaemon(settings)
            try:
                print(json.dumps(daemon.register(), indent=1, sort_keys=True))
                return 0
            finally:
                daemon.client.close()
        from mbnas_runner.daemon import main as daemon_main

        return daemon_main(settings, once=args.once, max_jobs=args.max_jobs)
    except RunnerConfigError as exc:
        print(f"mbnas-runner: {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except (CoreError, CoreUnavailable) as exc:
        print(f"mbnas-runner: core refused or unreachable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
