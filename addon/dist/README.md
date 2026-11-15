A LOCAL build's tarballs go here (the add-on's Dockerfile uses them instead of downloading the release):
`babyseat-<version>-<x86_64|aarch64>-unknown-linux-musl.tar.gz` and its `.sha256`, made by `scripts/dist/build.sh --archive` (see ../DOCS.md, "Build it yourself").
Only this file is committed (.gitignore).
