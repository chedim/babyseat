# stdlib: the world through the field (fs and proc)

Cliffhanger reaches the machine with two native drivers (#1044). Reads are getters; changes are writes; every
run leaves a receipt on its entity. On a seat with MEMBRANE_CLIFFHANGER_ONLY=1 this is the only way: there is no
shell (bash / code_run refuse).

## Reading (getters, never block the VM)
- `read(path)` -> the file's text
- `listdir(path)` -> [{name, kind, size, mtime}]
- `stat(path)` -> {exists, kind, size, mtime}

`~` is the home folder; a relative path is the seat's folder. A getter reads None until the answer lands, then
its value: give an entity a getter, e.g. a property `text` whose getter is `read("src/app.py")`, and read the
entity.

## Changing files (effects)
- `File:<name>.write = {id, content, append}`
- `File:<name>.move = {id, to}`
- `File:<name>.delete = {id}`

## Running programs (effects)
- `Command:<name>.run = {id, args}` -- the entity's `cmd` is an argument list (or a string run by `sh -c`),
  with optional `cwd` and `env_from {VAR: ENV_VAR}` (secrets are named, read at run time, never stored)
- `Command:<name>.stop = {id}` -- kills the running child

Each effect writes `<verb>_result = {id, status, rc, head, ...}` and `<verb>_rc` on the entity at the next round:
the whole stdout and stderr are kept, `head` is the first lines. No time limit, no output cap.

Define a Command once (its cmd, cwd) and run it again by writing `.run` -- the field remembers every run.

## Ready-made commands (the dev pack)
`Command:git` and `Command:gh` exist on every seat: run one by writing `.run = {id, args: [...]}`, e.g. `Command:git.run = {id: 'wt', args: ['-C', '<dir>', 'worktree', 'add', '<path>', '-b', '<branch>']}`. Their cwd is the seat's folder; name the repository with `-C <dir>` (git) or `-R <owner/repo>` (gh). Define your own Commands the same way.

## Not under /tmp
A File write, move or delete, and a Command that runs in or is given a path under `/tmp`, `/private/tmp`, `/var/tmp` or `/var/folders`, is refused (rc 2): a reboot or a battery restart wipes those folders and the work in them is lost. Work in the project's `.worktrees/<name>` (a git worktree) or another folder that survives a reboot. Reading a temporary file is fine.
