"""``python -m mbnas_core.owner_worker`` entry point (the former ``python -m mbnas_server.worker``; DD-A14).

The guard matters: an import walk (``pkgutil.walk_packages``) imports this module as ``mbnas_core.owner_worker.__main__``,
and an unguarded ``main()`` call would start a real worker on the configured DB (set-2 review incident S2-R1).
"""
from mbnas_core.owner_worker.main import main

if __name__ == "__main__":
    raise SystemExit(main())
