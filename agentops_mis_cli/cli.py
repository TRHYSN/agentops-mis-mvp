"""Console-script entry point for the dependency-free AgentOps CLI."""
from __future__ import annotations

import sys
import json
from typing import Sequence

from .agentops import main as agentops_main


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] == "host":
        if sys.platform.startswith("win"):
            print(json.dumps({
                "ok": False,
                "error": "host_not_supported_on_windows",
                "supported_windows_surfaces": ["agentops", "agentops-worker", "workspace_browser"],
                "next_step": "Connect this Windows client to a macOS or Linux AgentOps Host.",
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
        from .host import main as host_main

        return int(host_main(args[1:]))
    return int(agentops_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
