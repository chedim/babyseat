# membrane-seat is now babyseat

The project was renamed: install **babyseat** instead.

```
pip install babyseat
```

This package depends on `babyseat` at the same version, so
`pip install -U membrane-seat` moves an existing install over. (It carries a
copy of the same `membrane` module and command, so the upgrade's removal of the
old membrane-seat files cannot leave the install without them.) Nothing else
changes: the `membrane` command, the `membrane` Python module, `MEMBRANE_*`
environment variables and `.membrane` data directories keep their names, and
the new `babyseat` command is `membrane standalone`.

https://github.com/chedim/membrane
