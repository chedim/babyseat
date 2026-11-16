# Help:edit - change a file by a hunk, and take it back

An `Edit:<name>` is one change to one file: the text to replace (`old`), the text that replaces it (`new`), and the file (`path`). You send only the hunk and read back only the
diff, never the whole file. Nothing touches the disk until you write `apply`, so a draft is also the dry run.

## The properties you write

- `path`: the file (absolute, `~/...`, or relative to the seat's folder).
- `old`: the text to replace. It has to occur in the file exactly once; add a line or two of context until it does.
- `new`: the replacement. An empty text deletes the hunk.
- `base_mtime` (optional): the `mtime` of `info` as you read it. A file that has another mtime when you apply is refused, so an edit never lands on a file that changed under you.
- `apply = {id}`: make the change. `revert = {id}`: put the file back as it was before the edit.
- `seen = <anything new>`: read the file again (the edit reads it when `path` is written and after its own writes).

## The properties you read

- `problem`: what stops the hunk from being applied now, or nothing when it is ready: no path, empty `old`, no `new`, no such file, `old` not found, `old` matches N times, the file changed since you read it.
- `diff`: the hunk as `- old` and `+ new` lines.
- `hits`: how many times `old` occurs in the file now.
- `status`: `draft` (a problem is open), `ready`, `pending` (the file is being read again for your apply or revert), `applied`, `reverted`, `refused` (the reason is in `refused`), `failed` (the write did not land).
- `info` (`size`, `mtime`) and `text`: the file as it is now. `pre` and `post`: the file before and after the edit.

## A change, start to finish

```
Edit:greeting.path = "notes/hello.txt"
Edit:greeting.old = "hello world"
Edit:greeting.new = "hello there"
read Edit:greeting.diff, Edit:greeting.problem    -- a problem of nothing means the hunk is ready
Edit:greeting.apply = {"id": "a1"}
read Edit:greeting.status                         -- applied
Edit:greeting.revert = {"id": "r1"}               -- the file is as it was
```

A refused `apply` leaves the file as it is and names the reason in `refused`; fix the hunk (more context in `old`, a fresh `base_mtime`) and write `apply` again with a new id (an `apply` reads the file again first, so it is checked against the file as it is now).
A `revert` is refused when the file is no longer as the edit left it, so it never undoes somebody else's change.
To change the same file again, write a new `Edit:<name>`; each edit keeps its own pre-image.
