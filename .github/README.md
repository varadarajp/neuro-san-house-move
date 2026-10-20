# House Move Agent

A multi-agent assistant, built on [Neuro SAN Studio](https://github.com/cognizant-ai-lab/neuro-san-studio), that helps
someone moving home in the UK tell every organization that needs to know, in the right order and on time, without
sending anything they have not approved.

> EMEA Hackathon 2026 entry by associate 110737 (Varadaraj.pandurangan@cognizant.com, BFS UK).
> Synthetic customer data only. Every send is mocked and logged; nothing leaves the machine.

This repository is a complete Neuro SAN Studio project with the House Move agent added, so it runs straight from a
clone. Quick start:

```bash
uv venv venv --python 3.12
VIRTUAL_ENV=$PWD/venv uv pip install -r requirements.txt -r coded_tools/hackathon/house_move/requirements.txt
AGENT_MANIFEST_FILE=registries/manifest.hocon:registries/house_move_demo_manifest.hocon venv/bin/python -m neuro_san_studio run
venv/bin/python -m apps.house_move_upload.upload_server
```

The full write-up (design, agent network, privacy and safety, demo script, test customers, tests and data sources)
is in [docs/examples/hackathon/house_move.md](../docs/examples/hackathon/house_move.md).
