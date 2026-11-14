# Help:semtext - a text you can read by its sentences, words and the things it mentions

A `SemText` is an entity that holds ONE string, its `text`, and reads it for you: its sentences, words, identifiers, paths, urls and
numbers are properties of the entity, each a list of `{start, end, text}` (`start` and `end` count characters). Make one with a
constructor, in a pack that imports `semtext`; the target on the left is its id. Every example below is loaded and run by a test.

```c1f
IMPORT semtext 1
-- given Note:a {"say": "Dogs chase cats. See my_fn in ~/a.txt."}
-- write Note:a.keep = true
-- reads Note:a:body.sentences = [{"start":0,"end":16,"text":"Dogs chase cats."},{"start":17,"end":38,"text":"See my_fn in ~/a.txt."}]
-- reads Note:a:body.identifiers = [{"start":21,"end":26,"text":"my_fn"}]
-- reads Note:a:body.paths = [{"start":30,"end":37,"text":"~/a.txt"}]
Note.keep on-setter keep-body:
  ALWAYS:
    Note:a:body = SemText(text: this.say)
```

The layers are `sentences`, `words`, `identifiers`, `paths`, `urls` and `numbers`. They follow the text: write a new `text` and each one is read again.
Your own legs are SemTexts too: a command's output, a file's content when it changes, a page you fetched, your plan. Each gets the
same properties, and each can be mentioned (below). The chat makes one for every message you receive (`<message>:body`, a message is `Chat:main:message:<id>` or `Chat:session:<sid>:message:<id>`) and one for
every answer you give (`<message>:turn:response:body`); a tool's result is plain text.
Your earlier answers are entities you can read: `entity_read` of `<message>:turn:response`, property `text`. List the message ids with `entity_query`
`q: kind:ChatMessage` (oldest first; `offset` pages).

## A part of a text as an entity

Write `attach` on a SemText to get one part as an entity: `{layer: 'word', start: 5}` (layer is `sentence`, `word`, `identifier`, `path`, `url` or
`number`; `start` is the part's `start`). The part is `Span:of:<the text's id with :: written />:<w, s, i, p, u or n>:<start>`; its `text` is cut from the
text's, its `lemma` is the form the word is counted under, and its `parents` list names its form classes: `semplural` (`cats`), `semcapital`
(`Rex`, not at the start of a sentence), `semallcaps` (`NASA`), `semnumber`, `semidentifier`, `sempath`. A span lives while something holds it:
the receipt `attach_result` of the write holds the last one, and any entity that keeps it in a property holds it too.

```c1f
IMPORT semtext 1
-- given Note:a {"say": "Dogs chase cats."}
-- write Note:a.keep = true
-- write Note:a:body.attach = {"layer": "word", "start": 11}
-- reads Span:of:Note:a:body:w:11.text = "cats"
-- reads Span:of:Note:a:body:w:11.lemma = "cat"
-- reads Span:of:Note:a:body:w:11.parents = ["cls:semplural","cls:span"]
Note.keep on-setter keep-body:
  ALWAYS:
    Note:a:body = SemText(text: this.say)
```

A form class has getters of its own (`SemPlural.lemma` takes the plural suffix off), and the nearest class in `parents` answers first. A pack of yours that
imports `mentions` and defines `SemPlural.lemma` stands over the shipped rule and replaces it.

## What a text mentions

Write `mention = true` on a SemText and every word becomes an entity `Word:lemma:<lemma>` at its first mention (words fold by lowercase and their form
class: `Dogs` and `dogs` are `Word:lemma:dog`), each identifier an `Identifier:name:<text>` and each path a `Path:path:<text>`. The entity lists where it was
said in `mentioned_in`: `[{text: <the SemText>, sentence: <sentence number>}]`, one entry per sentence, and the text lists the entities it mentions in
`mentions`. Writing `mention` again changes nothing that is already there.

```c1f
IMPORT semtext 1
-- given Note:a {"say": "Dogs chase cats. Cats run."}
-- write Note:a.keep = true
-- write Note:a:body.mention = true
-- reads Word:lemma:cat.mentioned_in = [{"text":"Note:a:body","sentence":0},{"text":"Note:a:body","sentence":1}]
-- reads Word:lemma:chase.mentioned_in = [{"text":"Note:a:body","sentence":0}]
Note.keep on-setter keep-body:
  ALWAYS:
    Note:a:body = SemText(text: this.say)
```

## Related sentences

Which other sentences speak of the same things? Write how many you want in `related_n` of a SemText and read `related`: the sentences of the OTHER texts that
mention the same entities, best first, as `{semtext, sentence_index, text, shared}`. `shared` counts the entities that sentence has in common with your
text. The order weighs each shared entity by `1 / (the number of sentences that mention it)`: a word that every text says moves a sentence little, a word two texts
have between them moves it most. `related` is empty-handed (null) until you write `related_n`.

```c1f
IMPORT semtext 1
-- given Note:a {"name": "a", "say": "Quokkas eat grass."}
-- given Note:b {"name": "b", "say": "I saw a quokka."}
-- write Note:a.keep = true
-- write Note:b.keep = true
-- write Note:body:a.mention = true
-- write Note:body:b.mention = true
-- write Note:body:a.related_n = 3
-- reads Note:body:a.related = [{"semtext":"Note:body:b","sentence_index":0,"text":"I saw a quokka.","shared":1}]
Note.keep on-setter keep-body:
  ALWAYS:
    Note:body[this.name] = SemText(text: this.say)
```
