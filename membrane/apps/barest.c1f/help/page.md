# Help:page - what the page you are talking through loaded, and whether it works

The person sees the seat through a PAGE: a second copy of the field engine that runs in their browser and draws the screens. Nothing can ask it a question, so it TELLS: each time a page is loaded the seat gives it a name, `p` and eight hex digits (`p3fa91c0e`),
and the page writes what it is into the seat's field. Every open tab is its own page. Nothing is sent on a timer: a page that is working and quiet says nothing, and `seen` is the time the seat last heard from it.

- `entity_query` `kind:Vm` lists the pages (`Vm:<name>`, one row each). A restart forgets them: a tab that was open then tells again only when it is loaded again.
- `Vm:<name>` holds `caps` (what this page can do, written once), `seen` and `connected_at` (the seat's own time), `state` (`connected`, or `stale` when nothing was heard for the fact `link_stale_s` seconds: "not heard", not "gone"), `kind` (`page`).
- `<name>::Page:main` holds `report`, written again whenever one of its parts changes: `packs` (each pack the page runs and its size), `load_errors` (a pack that did not load: the page's own message, empty when all did), `view` (the screen in front), `hidden` (the tab is in the background), `width` (`narrow` or `wide`), `build` (the seat's build), `host_version` (the page's engine).
  Read the report with a `prop`: `run_cliff {code: "${'<name>::Page:main'}['report']"}`.

`caps` is the answer to "what can this page do?": `protocol`, `encodings`, `frames` (the ways it can talk: a write request over the door, a poll), `dom` (the tags and attributes a pack may draw with, and a hash of the list),
`provides` (the packs it runs, each as `{pack, major}`, with its `requires`: the packs it imports, and all they import in turn), `classes` (what its engine can evaluate), `limits`, `device` (`mobile` or `desktop`).
A pack may run on a page only if the page `provides` every pack in its `requires`; a pack that imports `dom` draws, so only a page can run it.

## Example: is the page fine?

List the pages, then read the newest one. A page is fine when `state` is `connected` and `load_errors` is empty; the `view` says what the person is looking at.

```c1f
-- given Vm:p3fa91c0e {"seen": 100}
-- write Vm:p3fa91c0e.caps = {"protocol": 1}
-- reads Vm:p3fa91c0e.kind = "page"
-- write p3fa91c0e::Page:main.report = {"view": "settings", "hidden": false, "load_errors": {}}
-- reads Vm:p3fa91c0e.state = "connected"
```
