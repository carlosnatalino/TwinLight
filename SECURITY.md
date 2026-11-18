# Security policy

## Scope and threat model

TwinLight is a research and laboratory tool. Authentication is opt-in: with
`TWINLIGHT_API_TOKENS` set, every REST request (except `GET /health`) and every
gNMI call needs one of those bearer tokens; without it, both interfaces are
open. Neither interface implements authorization scopes or TLS, so a token
travels in plaintext and any accepted token may do anything. Tokens keep
unintended clients off the twin, not attackers who can observe the network.
The web UI asks the user for a token when the twin needs one and keeps it in
the browser tab's session storage only, until the tab or browser is closed;
no token is ever delivered to it through its `config.js` or any other file.
On an open twin the `/admin/`, `/config/` and `/transparent/` planes let any
client with network access mutate twin state and write snapshot files.
`/transparent/` in particular can change every parameter the twin models.

The ONOS integration's T-API adapter (`integrations/onos/adapter`) is a
deliberate way around the token requirement. Given a token in
`TWINLIGHT_API_TOKEN`, it accepts requests on its port without credentials and
forwards them to the twin with that token, so anyone who can reach the port
can read the T-API context and its SIPs, list connectivity services, and
create and delete them. The Compose overlay publishes that port (8282) on the
host. Give the adapter a dedicated named token (an `onos:` entry in
`TWINLIGHT_API_TOKENS`) so it can be revoked alone, and limit who can reach the
port, for example by binding it to `127.0.0.1`. See
[integrations/onos/README.md § Authentication](integrations/onos/README.md#authentication).

**Do not expose a TwinLight instance to an untrusted network.** Run it on
localhost, inside a lab network, or behind an authenticating reverse proxy. The
Docker Compose stack publishes its ports on the host and is likewise intended
for local use; the Grafana instance ships with the well-known `admin`/`admin`
credentials, which must be changed for any deployment that outlives an
experiment.

Within that model, the following are *not* considered vulnerabilities:

- Unauthenticated access to any endpoint when no API tokens are configured,
  and to `GET /health` when they are.
- State mutation through `/config/`, `/transparent/` or `/admin/` by any client
  that can reach the port (no tokens) or holds any accepted token.
- Access to the twin through the ONOS adapter, with the adapter's token, by any
  client that can reach the adapter's port.
- Token disclosure through observation of the unencrypted network.
- Denial of service caused by requesting propagation over a very large topology.

## Reporting a vulnerability

Please report anything that *does* fall outside the model above — for example a
path-traversal escape from the snapshot directory, remote code execution through
a crafted topology or configuration file, or a dependency vulnerability that
reaches TwinLight's code paths.

Use GitHub's private vulnerability reporting on the
[Security tab](https://github.com/carlosnatalino/TwinLight/security/advisories/new)
rather than opening a public issue. Please include the version or commit, a
description of the impact, and reproduction steps.

You can expect an acknowledgement within a couple of weeks. This is a
research project maintained alongside other work, so please be patient with
timelines; fixes land on `main` and are noted in the release that follows.

## Supported versions

Only the latest commit on `main` is supported. There is no back-porting to
earlier tags.
