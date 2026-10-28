# b649: the per-turn block A/B (#649, #532)

`harness.py` boots a throwaway seat (serve :4196, proxy :11596), with watch loops, the thinker, the dreamer, the night clock and the autonode off. It asks fresh-chat questions under each injection arm and scores the answers by exact substrings, with no judge.

- `setup <dir>`: run the clean field's history chats (`topics.json`, fictional facts) once; `<dir>/base` is the snapshot.
- `ask <dir> <arm>`: APFS-clone `base` to `<dir>/<arm>` and ask. Arms: `none`, `conversation`, `entities`, `ids`, `facts`, `massage`. `@N` sets the char budget (`MEMBRANE_EXP_BUDGET`); `-t` allows membrane's memory tools. `+nc` runs the seat with `--no-context` (the transcript is stripped and the field's reconstruction of recent turns rides). `+ncp` strips the reconstruction too (`MEMBRANE_EXP_NORECON=1`), so the model sees only the recall block and the current message. Example: `facts@4000+ncp`.
- `score <dir>`: the table (correct, block size, tokens per turn, tool calls).
- Live field: put an online SQLite backup of a seat's `field.cblite` plus `entities.json` in `<dir>/base`, and set `B649_TOPICS=live_topics.json`. Never commit field data.

The `entities` / `ids` / `facts` modes and `MEMBRANE_EXP_DUMP` (each turn's injected block) live in `membrane/lanes.py` on this branch only. They are an experiment, not for merge.
