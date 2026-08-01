"""Console-script entry point for the dependency-free AgentOps CLI."""
from __future__ import annotations

import sys
from typing import Sequence

from .agentops import main as agentops_main
from .host import main as host_main


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] == "host":
        return int(host_main(args[1:]))
    return int(agentops_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
