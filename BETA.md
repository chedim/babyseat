# Membrane private beta (0.3.0)

Thanks for testing. You have access to this repository; that is all you need.
Everything else (PyPI, HACS) comes later, when the repository goes public.

**Before you start:** an [Ollama](https://ollama.com) server reachable from where
membrane runs, with one chat model and one embedding model
(`ollama pull qwen3:8b && ollama pull mxbai-embed-large`, or any cloud model
Ollama serves for chat), and 1–3 GB of free RAM.

## A. Standalone (the beta's primary path)

**A1. The zip (easiest)** — download
[`membrane-standalone-0.3.0.zip`](https://github.com/chedim/membrane/releases/download/v0.3.0/membrane-standalone-0.3.0.zip)
and follow its `INSTALL.txt`:

```bash
unzip membrane-standalone-0.3.0.zip && cd membrane
pip install ./membrane_seat-0.3.0-py3-none-any.whl
mkdir ~/bob && cd ~/bob
membrane standalone            # opens http://localhost:4096/
cp -R <unzipped>/assets ~/bob/.membrane/      # the voice files (~/.membrane/assets)
```

**A2. From the repository** (needs SSH keys set up with GitHub):

```bash
pip install "git+ssh://git@github.com/chedim/membrane.git@entity-field"   # or uv tool install ...
mkdir ~/bob && cd ~/bob
membrane standalone            # opens http://localhost:4096/
```

For voice, either zip above comes with it (`~/bob/.membrane/assets/`); from the
repository alone, download `membrane-voice-0.3.0.zip` from the
[releases](https://github.com/chedim/membrane/releases) and unzip it there.
Details: [docs/standalone.md](docs/standalone.md).

**Updating:** `membrane update` for a repository install (reinstalls from the
same branch); a zip install is replaced by the next release's zip. Then restart.

## B. Inside Home Assistant (optional)

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

## What to try

- Say hi — it's **Bob** until you agree on a name; it will ask.
- "Hey Bob, …" with the ear (👂) on — the request in the same breath as the name.
- Ask it about your home (Home Assistant) or anything; watch the field move.

## Reporting

[Issues](https://github.com/chedim/membrane/issues): what you did, what you
expected, what happened. Useful attachments: `.membrane/opencode.log` and
`.membrane/page-voice.log` (standalone), `<config>/membrane/seat.log` (Home
Assistant). Your Bob knows about the issues too — when it finds a bug in
itself it drafts one and gives you the link; check it for private details before
filing.