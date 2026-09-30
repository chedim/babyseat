"""membrane: a shared entity field as long-term memory for coding agents.

Every turn's text is embedded as EVENTS on an entity field (Couchbase
collections), where every word/phrase/identifier is an entity moved by the
mentions that name it; recall, replay and injection read the field's live
entity states. The field is the memory; the LLM is the only escalator.

See docs/entity-field.md for the design and the experiments behind it.
Real surface: python -m membrane.opencode (the seat / proxy).
"""