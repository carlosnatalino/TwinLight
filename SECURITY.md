# Security policy

## Scope and threat model

TwinLight is a research and laboratory tool. Neither the REST API nor the gNMI
interface implements authentication, authorization or TLS, and the `/admin/` and
`/config/` planes let any client with network access mutate twin state and write
snapshot files.

**Do not expose a TwinLight instance to an untrusted network.** Run it on
localhost, inside a lab network, or behind an authenticating reverse proxy. The
Docker Compose stack publishes its ports on the host and is likewise intended
for local use; the Grafana instance ships with the well-known `admin`/`admin`
credentials, which must be changed for any deployment that outlives an
experiment.

Within that model, the following are *not* considered vulnerabilities:

- Unauthenticated access to any endpoint.
- State mutation through `/config/` or `/admin/` by any client that can reach
  the port.
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
