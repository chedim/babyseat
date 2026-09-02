---
description: Toggle the [membrane] banner (gate verdict, field vitals, memory flow) in the thinking channel. Direct proxy call - no model turn spent.
---

Run this exact shell command and show its output verbatim:

```bash
curl -s -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"membrane/x","stream":false,"messages":[{"role":"user","content":"membrane: monologue $ARGUMENTS"}]}' | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

That is all — one curl, one line of output. Do not interpret, do not add commentary.