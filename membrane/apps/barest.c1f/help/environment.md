# Help:environment - the environment variables of the seat

The variables the seat was started with are ONE entity, `Environment:host`, and its PROPERTIES ARE the variables: `Environment:host.HOME`, `Environment:host.LANG`, `Environment:host.TMPDIR`. The property name is the variable name, capitals, digits and
underscores as they are. A variable that is not set is absent: reading it gives a null with the reason `absent: ...`, so `or` gives a default.

Only a short list of names carries a value: `HOME`, `USER`, `LANG`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `TMPDIR`, `CLAUDE_BIN` and the seat's data-folder variable. The launch fact `env_allow` adds names (a list, or text with commas). Every other variable is absent.
A name that holds KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL (any case) is a credential: its property reads the text `set` when it is set, and never its value. Ask for one in `env_allow` and the start says it is refused. `Environment:host.ANTHROPIC_API_KEY` answers
"is the key there?" and nothing more.

The entity is written once, at the start, and stored: a restart shows the values of the new start, and a variable that is no longer set is gone.

```c1f
PACK where_am_i 1
Place.dir getter: Environment:host.HOME or '.'
Place.key_ready getter: Environment:host.ANTHROPIC_API_KEY == 'set'
-- given Environment:host {"HOME": "/home/ann"}
-- given Place:a {"name": "here"}
-- reads Place:a.dir = "/home/ann"
-- reads Place:a.key_ready = false
```
