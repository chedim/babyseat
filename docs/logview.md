# The boundary log as a view (`membrane/packs/logview.c1f`)

What entered the VM, read from a cursor by a peer (membrama, any program over shared memory). The journal (`c_journal`) holds every OUTSIDE write in order: a person's message, a driver's receipt, a write through the door; each line says who wrote it (`writer`). What the getters derived inside the VM is not in it (a replay makes it again), and nothing virtual is: the peer reads the interior itself, with the door's tools or a subscription.

A view `membrane::LogView:<ns>` appears for a peer when it ASKS for one, by writing `<ns>::LogAsk:main.n` (`VIRTUAL`: a window on the journal, never stored; the resident `LogViews:seat` holds it by reference). No fact names the peers: any connected VM can ask, a peer or a page. A page asks the same way, with the door (`entity_write` of `<its ns>::LogAsk:main`, a page's `Fetch`), and reads the view by its link: `head` and `rows` of `membrane::LogView:<its ns>` are subscribed values (the allow-list `LinkAllow:logview`; a virtual entity's values are carried like any other, since #1143 `link_route.c1f` reads the `membrane::` twin of a name the route stripped).

**Who is connected** (`Peers:seat.up`, packs/link.c1f; the link allow-list `LinkAllow:log` lets a page follow it): the namespaces of the peers whose link is up. A tab for a peer follows it, so the tab comes and goes with the peer.

**The seat's own Log tab** (`membrane/apps/barest.c1f/log_view.c1f`, a page pack, `Nav:log`): the latest 200 lines of this log, asked by the page as above (older lines in pages of 200), and under them the raw chat with the model, `ModelChat:main` (docs/model-chat.md). It needs no peer. A peer that has more to show draws its own section into the tab (DomNodes parented to `DomNode:log:root` while `Nav:log` is active); it does not write the seat's nodes. Test: `tests/js/log_view.test.mjs`.

**The cursor is the peer's, in its own namespace**, so the seat keeps nothing about the reader and no write rule is needed: the peer writes `<ns>::LogAsk:main` with `from` (the number of the first line it wants; 0 is the beginning) and `n` (a count of its asks). A `sql` answer is cached by its query and arguments, so `n` is the cache breaker: each ask is a new key and a fresh read of the journal.

The view answers with cells:
- `rows`: the page, a list of records `{n, eid, prop, op, writer, ts, size}`: which entity and property changed, by whom, and how big the new value is (`size`). NOT the value: the peer reads the entity. A node that is retired is `op: "gone"`. A virtual node the engine collects is not a row (it was never stored).
- `next`: where the following page starts (`from` plus the rows of this one).
- `head`: the number of the last line.

A page is at most 200 rows (the owner's number), about 30 KB: the peer's `val_cap` (`docs/membrama-chat.md`, the peer) must be above it, or the cell is `Refused` and says so.

Test: `seat-tests/tests/peers_1143.rs`, `a_peer_reads_the_journal_as_records_from_its_own_cursor` (a fake peer pages through the log, finds a door write with its writer, and sees a retired node's `gone` row).
