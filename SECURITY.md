# Security

AutoRundown runs on a shop PC and is reached from phones on the shop's own
network. Its security model (who is the PC, who is a mechanic, what a phone can
and cannot do) is written down in
[`docs/notes/shop-network.md`](docs/notes/shop-network.md).

## Reporting a problem

Please report a vulnerability **privately**, not in a public issue:
use GitHub's **Security → Report a vulnerability** on this repository.

Include what you did, what you expected and what happened; a request or a few
steps that reproduce it is ideal. You will get an answer, and a fix before any
public write-up where the problem is real.

Things that matter most here: a device that is not the PC reaching Shop setup,
the API keys or the index; one mechanic seeing another's questions or spend;
a manual's text running as script in the page; a path or request that reads a
file outside the manual folders.

## What is not a vulnerability

- Anyone on the same network as the PC can open the sign-in page. That is the
  design: sign-in (on by default) is what protects searching and asking.
- The installer is unsigned, so Windows SmartScreen warns on first run.
- The app serves plain HTTP on the shop network unless it is started with
  `--https`. Keep customers off that network; see the README.

## Supported versions

The latest release. Older ones get no fixes; upgrading keeps your index and
settings (they live outside the install).
