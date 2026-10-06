# my-notion — origin

`my-notion` is an independent capability. Its first version, on 2026-08-10, started from the `notion` capability in the public `ai-cluster-one/capabilities` catalogue. Everything since is ours, and the two have diverged well past the point where a merge would mean anything.

Measured 2026-09-07 against that catalogue's `notion`: 448 of our 4692 lines survived verbatim from the 1056-line base, about 9% of the file, and we carried 29 `cmd_*` handlers against its 7. The additions are the database, view, user, comment, attachment and archive surfaces, plus the `2026-03-11` data-source API pin.

There is no upstream sync and no baseline to diff against: the catalogue's maintainer develops `notion` on his own line and asked that we run an independent capability rather than a fork.
