# Membrane private beta (0.3.0)

Thanks for testing. You have access to this repository; that is all you need.
Everything else (HACS, PyPI) comes later, when the repository goes public.

**Before you start:** an [Ollama](https://ollama.com) server reachable from where
membrane runs, with one chat model and one embedding model
(`ollama pull qwen3:8b && ollama pull mxbai-embed-large`, or any cloud model
Ollama serves for chat), and 1–3 GB of free RAM.

## A. Inside Home Assistant

1. From the [releases](https://github.com/chedim/membrane/releases), download
   `membrane-ha-0.3.0.zip` (the integration with membrane and its voice files
   inside — nothing else gets installed).
2. Unzip it into `<config>/custom_components/` — you get
   `<config>/custom_components/membrane/` — and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Membrane → Inside Home
   Assistant*. With Home Assistant's Ollama integration it's found by itself;
   otherwise give Ollama's address. Pick the models.

After a minute or two Home Assistant opens on membrane home. The details are
in [docs/home-assistant.md](docs/home-assistant.md) — please read *What the seat
may do in your house*: by default it can read states and call services.

**Updating:** replace `custom_components/membrane/` with the new zip's, restart.

## B. Standalone (its own web UI)

```bash
pip install "git+ssh://git@github.com/chedim/membrane.git@entity-field"   # or uv tool install ...
mkdir ~/bob && cd ~/bob
membrane standalone            # opens http://localhost:4096/
```

For voice, download `membrane-voice-0.3.0.zip` from the releases and unzip it into
`~/bob/.membrane/assets/` (the seat can't fetch it from a private repository).
Details: [docs/standalone.md](docs/standalone.md).

**Updating:** `membrane update` (reinstalls from the same branch), then restart it.

## What to try

- Say hi — it's **Bob** until you agree on a name; it will ask.
- "Hey Bob, …" with the ear (👂) on — the request in the same breath as the name.
- Ask it about your home (Home Assistant) or anything; watch the field move.

## Reporting

[Issues](https://github.com/chedim/membrane/issues): what you did, what you
expected, what happened. Useful attachments: `<config>/membrane/seat.log` (Home
Assistant) or `.membrane/opencode.log` and `.membrane/page-voice.log`
(standalone). Your Bob knows about the issues too — when it finds a bug in
itself it drafts one and gives you the link; check it for private details before
filing.
