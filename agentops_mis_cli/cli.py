"""Console-script entry point for the dependency-free AgentOps CLI."""
from __future__ import annotations

import sys
from typing import Sequence

from .agentops import main as agentops_main


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] == "host":
        if sys.platform == "win32":
            sys.stderr.write(
                "agentops host is not supported on Windows; "
                "use the generic AgentOps CLI or OpenCekura Windows doctor instead.\n"
            )
            return 2
        from .host import main as host_main

        return int(host_main(args[1:]))
    return int(agentops_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
