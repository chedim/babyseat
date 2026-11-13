# Help:c1f - the language of the field (cliffhanger, c1f)

A pack is a text of getters and setters over entities. A getter is a cached value that recomputes when what it read changes. A
setter runs when its property is written. Acting is writing. Every code block below is loaded and run by a test, so it is real.

## Getters

```c1f
-- given Box:a {"w": 2, "h": 5}
-- reads Box:a.area = 10
Box.area getter: this.w * this.h
```

`Box.area` is the property `area` of every entity of class `Box`; `this` is the entity. Arms choose by condition, the first
`WHEN` that holds wins, `ELSE:` is the default:

```c1f
-- given Box:a {"w": 2, "h": 5}
-- reads Box:a.size = "small"
-- write Box:a.w = 9
-- reads Box:a.size = "big"
Box.size getter:
  WHEN this.w * this.h > 20: 'big'
  ELSE: 'small'
```

A getter body is a BLOCK: one line `name = expr` makes a local (visible to the lines below it, in that block only, never stored),
and the last line is the value:

```c1f
-- given Box:a {"w": 2, "h": 5}
-- reads Box:a.total = 11
Box.total getter:
  base = this.w * this.h
  tax = base / 10
  base + tax
```

## Defaults and nulls

A default is `or`: `x or d` is `d` when `x` is null, False, 0, empty text or an empty list. To default a null alone, test it with
`== None`:

```c1f
-- given Box:a {}
-- given Box:b {"note": "hi", "n": 0}
-- reads Box:a.label = "none yet"
-- reads Box:b.label = "hi"
-- reads Box:b.n_or_seven = 7
-- reads Box:b.n_or_seven_if_null = 0
Box.label getter: this.note or 'none yet'
Box.n_or_seven getter: this.n or 7
Box.n_or_seven_if_null getter: 7 if this.n == None else this.n
```

`??` is JavaScript; c1f reads it as an unexpected `?` and the pack is refused with `unexpected '?'`. The c1f spelling is `or`:

```c1f-error
-- error: unexpected '?'
Box.label getter: this.note ?? 'none yet'
```

A property that is missing reads as a null that carries its REASON; an operation on a null is that same null, so the reason travels
along the getters that pass it on (the engine's `Why` chain follows it back to where it began). `None('reason')` makes a null with a
reason of your own; `or` supplies the value that replaces it:

```c1f
-- given Box:a {}
-- reads Box:a.total = null
-- reads Box:a.safe = 1
-- reads Box:a.source = null
Box.total getter: this.price * 2
Box.safe getter: (this.total or 0) + 1
Box.source getter: None('no path named yet') if this.path == None else this.path
```

Casts check a type and give a reasoned null otherwise: `Int(x)`, `Float(x)`, `Num(x)`, `Text(x)`, `Bool(x)`, `List(x)`, `Dict(x)`.

## Operators and builtins

Arithmetic `+ - * /` (`+` joins text), comparison `== != < <= > >=`, `and or not`, `in` and `not in`, the conditional
`a if cond else b`, list comprehensions `[f(x) for x in xs if cond]`, index `xs[0]` and `d['k']`, list and dict literals.
Constants are `True False None`; `clock` is the time in epoch seconds.

```c1f
-- given N:a {"a": 7, "b": 2, "name": "  Ada Lovelace "}
-- reads N:a.sum = 9
-- reads N:a.quo = 3.5
-- reads N:a.big = true
-- reads N:a.both = true
-- reads N:a.member = true
-- reads N:a.said = "a is 7"
-- reads N:a.pick = "hi"
-- reads N:a.doubled = [4, 6]
-- reads N:a.second = 20
-- reads N:a.key = 3
-- reads N:a.first = "ada"
-- reads N:a.count = 2
-- reads N:a.largest = 7
N.sum getter: this.a + this.b
N.quo getter: this.a / this.b
N.big getter: this.a > this.b
N.both getter: this.a > 1 and not (this.b > 5)
N.member getter: this.a in [7, 8]
N.said getter: 'a is ' + str(this.a)
N.pick getter: 'hi' if this.a > 5 else 'lo'
N.doubled getter: [x * 2 for x in [1, 2, 3] if x > 1]
N.second getter: [10, 20][1]
N.key getter: {'k': 3}['k']
N.first getter: lower(words(strip(this.name))[0])
N.count getter: len(words(this.name))
N.largest getter: max([this.a, this.b])
```

Builtins: `len abs min max lower strip replace startswith lines words join head sort_by before after pluck distinct concat json str num
prop why instances referencing`, plus the readers `sql read listdir stat`. The operators `//`, `%`, `**`, `@`, `is` and `lambda` are
refused when the pack loads:

```c1f-error
-- error: not allowed
Box.rest getter: this.a % 2
```

## Entities, reads and loops

`instances('Box')` is every Box. Reading `b.w` or `prop(b, 'w')` takes a property of an entity:

```c1f
-- given Box:a {"w": 1}
-- given Box:b {"w": 4}
-- given Tally:t {}
-- reads Tally:t.biggest = 4
-- reads Tally:t.count = 2
Tally.biggest getter: max([b.w for b in instances('Box')])
Tally.count getter: len(instances('Box'))
```

An entity written without quotes is an entity: `Goal:board`, `app::Config:voice:in`. A quoted name works the same way:
`('Goal:' + slug).status = 'open'`. `[key]` after an entity literal appends a segment: `Book:copy[slug]` is `Book:copy:<slug>`.

## Setters

`Class.prop on-setter name:` runs when `prop` is written; `WHEN`, `ELSE WHEN`, `ELSE:` choose; `ALWAYS:` has no condition. A setter's
lines are writes (`target.prop = value`), locals, and `FOR x IN list:` loops over writes:

```c1f
-- given Lamp:a {}
-- write Lamp:a.power = "on"
-- reads Lamp:a.lit = true
-- write Lamp:a.power = "off"
-- reads Lamp:a.lit = false
Lamp.power on-setter light:
  WHEN this.power == 'on':
    this.lit = True
  ELSE:
    this.lit = False
```

A write can reach another entity, and a loop writes to several:

```c1f
-- given Hall:h {}
-- given Lamp:a {}
-- given Lamp:b {}
-- write Hall:h.mode = "night"
-- reads Lamp:a.dim = true
-- reads Lamp:b.dim = true
Hall.mode on-setter dim-lamps:
  WHEN this.mode == 'night':
    FOR l IN instances('Lamp'):
      l.dim = True
```

A constructor makes an entity: `Class(name: value)` on the right of a write whose left names the entity (the target is the
identity; the same target is the same entity, made once):

```c1f
-- given Shelf:s {}
-- write Shelf:s.add = "Dune"
-- reads Book:copy:dune.title = "Dune"
Book.label getter: this.title
Shelf.add on-setter add-book:
  ALWAYS:
    slug = lower(this.add)
    Book:copy[slug] = Book(title: this.add)
```

`target.[expr] = value` writes the property whose NAME the expression gives. `this.retire = True` removes an entity.

## Effects are writes

A driver is an entity class whose verb property is the effect; its receipt lands as `<verb>_result` on the same entity:

```c1f
IMPORT proc 1
Job.go on-setter start:
  ALWAYS:
    Command:build.run = {'id': 'b1', 'args': ['ls', '-l']}
Job.report getter: Command:build.output
```

`Command:<name>.run = {id, args}` runs a program (receipt `run_result`, `run_rc`, getter `output`). `File:<name>.write`,
`Fetch:<name>.request` and `Store:<name>.run` follow the same shape: write, then read the receipt back.

## Where things live

- A pack text: `Help:packs`. What changed lately and a SQL / Vue / JS lookup table: `Help:upgrade`.
- Read an entity with entity_read (`Help:c1f`), list the topics with entity_query `kind:Help`.
