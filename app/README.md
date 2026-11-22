# Arity for Mac

The SwiftUI front end. All logic lives in the Rust core (`../core`); the app
calls it through Swift bindings that UniFFI generates at build time.

```sh
app/build.sh             # build core + bindings + app, then open it
app/build.sh --no-open   # build only
```

The CLI is the other front end over the same core:

```sh
cargo run -q -p arity-cli -- help
```

Both read and write `~/Library/Application Support/Arity` (or `$ARITY_HOME`),
and the app polls for changes, so a conversation driven from the CLI shows up
in the open window as it happens.

Development aids: `ARITY_SNAPSHOT=/path/shot.png` makes the app save a PNG of
its window every two seconds; `ARITY_OPEN=<conversation>` selects one at launch.
Run the binary directly to pass them:
`ARITY_SNAPSHOT=/tmp/a.png app/build/Arity.app/Contents/MacOS/Arity`.
If your own Arity is already open, add `-ApplePersistenceIgnoreState YES` after
the binary (and point `ARITY_HOME` at a scratch folder), so the second copy
neither restores nor overwrites the first one's windows.
