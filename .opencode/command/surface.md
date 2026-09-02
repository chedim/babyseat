---
description: Show the totality of the membrane<->model API surface - the last turns of everything membrane sent to / received from the model. Direct proxy call, no model turn spent.
---

Run this exact shell command and show its output verbatim:

```bash
curl -s -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"membrane/x","stream":false,"messages":[{"role":"user","content":"membrane: surface"}]}' | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

That is all - one curl, one output. Do not interpret, do not add commentary.