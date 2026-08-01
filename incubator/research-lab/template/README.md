# Research Lab Embedded Template Contract

This directory is the first bounded foundation for installing Research Lab into the same AgentOps MIS Workspace/AppShell.

Validate the canonical manifest and reference instance:

```bash
python3 incubator/research-lab/template/scripts/validate_template_manifest.py \
  --manifest incubator/research-lab/template/manifest.json \
  --instance incubator/research-lab/template/examples/building-wireframe-lab.instance.json
```

Run tests:

```bash
python3 -m unittest \
  incubator/research-lab/template/tests/test_template_manifest.py
```

The foundation intentionally does not implement external connector clients or a training scheduler. It fixes the contract that later MIS adapters and UI routes must obey.
