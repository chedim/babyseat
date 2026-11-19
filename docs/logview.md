# The boundary log as a view (`membrane/packs/logview.c1f`)

What entered the VM, read from a cursor by a peer (membrama, any program over shared memory). The journal (`c_journal`) holds every OUTSIDE write in order: a person's message, a driver's receipt, a write through the door; each line says who wrote it (`writer`). What the getters derived inside the VM is not in it (a replay makes it again), and nothing virtual is: the peer reads the interior itself, with the door's tools or a subscription.

For each peer declared in the fact `peers` there is a view `membrane::LogView:<ns>` (`VIRTUAL`: a window on the journal, never stored; the resident `LogViews:seat` holds it by reference).

**The cursor is the peer's, in its own namespace**, so the seat keeps nothing about the reader and no write rule is needed: the peer writes `<ns>::LogAsk:main` with `from` (the number of the first line it wants; 0 is the beginning) and `n` (a count of its asks). A `sql` answer is cached by its query and arguments, so `n` is the cache breaker: each ask is a new key and a fresh read of the journal.

The view answers with cells:
- `rows`: the page, a list of records `{n, eid, prop, op, writer, ts, size}`: which entity and property changed, by whom, and how big the new value is (`size`). NOT the value: the peer reads the entity. A node that is retired is `op: "gone"`. A virtual node the engine collects is not a row (it was never stored).
- `next`: where the following page starts (`from` plus the rows of this one).
- `head`: the number of the last line.

A page is at most 200 rows (the owner's number), about 30 KB: the peer's `val_cap` (`docs/membrama-chat.md`, the peer) must be above it, or the cell is `Refused` and says so.

Test: `seat-tests/tests/peers_1143.rs`, `a_peer_reads_the_journal_as_records_from_its_own_cursor` (a fake peer pages through the log, finds a door write with its writer, and sees a retired node's `gone` row).
