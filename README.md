# Elder Sign

> Ward your posts against troll swarms.

Elder Sign watches the comments on your reels and quietly hides or deletes
the hateful ones within minutes, so you almost never have to see them and
never have to clean up after a swarm yourself. Trolls keep arguing with a
void; your audience sees the people who showed up with love.

It is built on [Azathoth](https://github.com/EldritchInc/azathoth), and it
is deterministic first: a model is asked about a comment only when nothing
cheaper can decide it, and every answer is remembered. A scan of
copy-pasted swarm comments it has already seen uses no inference at all.

**Status:** pre-alpha, under construction.

## How it decides

Each comment goes through these layers in order, and the first one that
knows the answer decides:

1. **Trusted authors:** your friends are never touched.
2. **Blocked authors:** known trolls are hostile on sight.
3. **Blocked terms:** slurs match after normalization, so `tr4nnyyy`,
   `t r a n n y` and Cyrillic look-alikes are the same word.
4. **Remembered content:** a comment or GIF a model already judged hostile or
   friendly in any thread.
5. **Remembered context:** a reply a model already judged in reply to the
   same comment, for replies like "lol exactly" whose meaning depends on what
   they answer.
6. **A model**, only for what is left. Identical comments in one scan are
   asked about once.

## Working on Elder Sign

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make check
```

Tool sources live in `src/elder_sign/tools/` as ordinary Python. `make tools`
builds the Azathoth tool documents in `src/elder_sign/documents/tools/` from
them, inlining the shared modules each tool imports, and `make check` fails
if a document has drifted from its source.

## License

MIT
