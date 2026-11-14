THE FIELD ENGINE IN THE PAGE: the babyseat page runs the same field engine as the seat, compiled to WebAssembly (fieldvm.js, in the page's folder). Every open page follows the seat: it loads the seat's packs and their entities, then applies each write as it happens.

- HOW CODE REACHES IT: as a pack. A pack that has `IMPORT dom 1` is a view pack: it runs in the page, not on the seat. Load it like any pack, exec(action='load', ...) (help('packs')); every open page picks it up at once. (JS through exec lang='js' is the other path: help('browser').)
- WHAT IT SEES: the page's whole DOM is in the field. Each element is `DomNode:page:#<id>` (`DomNode:page:<n>` without an id) with page=true, `tag`, `parent`, `id`, its own `text` and the allow-listed attributes; new elements are born, changes are written, removed ones leave. The chat is `DomNode:page:#chat`. A click on a node writes its `clicked`.
- WHAT IT MAY DO: make its own nodes (`DomNode:<key>` with `tag`, `text`, `parent`) and put them inside page elements, write `text` (plain text, never HTML) and allow-listed attributes (links http(s) only). A round's DOM writes land together, once per frame. Toward the seat the page is read-only: what its packs write stays in that page.
- WHAT COMES BACK: nothing to your turn -- a view pack shows, it does not answer. Read what it made by its fields on the seat's copy of the pack.
- RULES: it acts as the person looking at the page: show and draw, never device control or service calls.

Example, a row per task under the chat that finishes the task when clicked (the shape tests/js/fieldvm_core.test.mjs runs):
  PACK taskrows 1
  IMPORT goalgraph 1
  IMPORT dom 1
  Task.status on-setter row:
    ALWAYS:
      ('DomNode:taskrows:' + this.eid).parent = 'DomNode:page:#chat'
      ('DomNode:taskrows:' + this.eid).task = this.eid
      ('DomNode:taskrows:' + this.eid).text = this.name + ': ' + this.status
  DomNode.clicked on-setter finish:
    WHEN this.task: this.task.status = 'done'
