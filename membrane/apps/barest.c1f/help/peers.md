# Help:peers - programs and other seats linked to this one

A program or another seat that shares this graph is a PEER. It is declared by the owner (the boot fact `peers`) and shows up here as an entity of its own.

- `entity_query {q: "kind:Vm"}` lists every link: the pages and the peers. A peer has `kind` `peer`, `link` (`up` while its program answers, `gone` when it left), `state` (`connected` or `gone`), `peer_name` and `peer_build`. A peer that left stays in the list as `gone`; it is not deleted.
- `entity_read {spec: "Vm:<name>"}` names what one holds; `run_cliff {code: "Vm:<name>.link"}` reads a value.
- A node a peer owns is `<name>::<Class>:<id>`: the peer's name, then two colons. A node of this seat has no prefix. Read a peer's node by its prefixed name: `run_cliff {code: "kitchen::Lamp:desk.power"}`.
- The first read of a peer's node answers `pending`: the seat is asking the peer for exactly that prop. Read again after a moment and the value is there; a rule that reads it runs again by itself when it arrives, and again at every change.
- A peer's nodes are read here and changed by their owner. A peer that asks to write a node of this seat is decided by the boot fact `peer_writes`, a list of `{peer, prefix}` rules: the peer may write the nodes whose id starts with `prefix`, and with no rule the write is refused with the reason. A peer whose token GRANTED it the right (the claim `grants`: a list of id prefixes; the entry `*` is every prefix) is also accepted; the refusal names both the fact `peer_writes` and the peer's grants. `*` includes the privileged classes (Pack, Command, File, ...).
- A name is the peer's own namespace. Two seats are told apart by the name each gives the other (`rosie::Lamp:desk` here is `Lamp:desk` there).

## Example: is a peer connected?

List the links, then read the peer by its name. It is connected when `link` is `up`.

```c1f
-- given Vm:kitchen {"link": "up"}
-- reads Vm:kitchen.kind = "peer"
-- reads Vm:kitchen.state = "connected"
```

A peer whose program left is `gone`, and comes back `up` by itself when its program is back:

```c1f
-- given Vm:garage {"link": "gone"}
-- reads Vm:garage.kind = "peer"
-- reads Vm:garage.state = "gone"
```
