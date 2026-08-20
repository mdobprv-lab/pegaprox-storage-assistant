# Security Policy

## Project status

PegaProx Storage Assistant 0.1.1 is an early community preview. It validates and
stores resource definitions and generates non-executing deployment plans. It does
not discover, connect, mount, format or register remote storage.

Remote execution is deliberately disabled in this release and cannot be enabled
through `config.json` or the plugin UI.

## Supported versions

| Version | Security support | Notes |
|---|---|---|
| Latest 0.1.x | Best effort | Definition-only preview; upgrade to the newest patch release before reporting |
| Older versions | No | Preview data formats and API contracts may change |

## Reporting a vulnerability

Use **Security → Report a vulnerability** in this GitHub repository. This creates a
private vulnerability report that can be reviewed without exposing users before a
fix is available.

If private reporting is temporarily unavailable, open a minimal public issue asking
for a private contact channel. Do not include exploit details, credentials, target
addresses, storage identifiers or production logs in that issue.

Include, when available:

- affected plugin and PegaProx versions;
- deployment model (native, Docker or package installation);
- the smallest safe reproduction;
- expected and observed authorization boundaries;
- whether secrets, remote execution or destructive storage operations are involved;
- sanitized logs and configuration fragments.

## Current security boundaries

Version 0.1.1 is designed around the following constraints:

- `execution_enabled` is hard-coded to `false`;
- `config.json` accepts only language and theme preferences;
- CHAP secrets, SSH credentials and PVE/PBS API tokens are not stored in the
  resource registry;
- PVE content types are limited to `iso`, `vztmpl`, `snippets` and `import`;
- resource creation and updates require manager-level authorization;
- definition changes are submitted to the PegaProx audit log;
- registry updates use cross-process locking and atomic replacement;
- duplicate NFS destinations, PBS datastores, target/LUN ownership and WWIDs are
  rejected;
- a LUN definition has exactly one PBS owner;
- NFS path traversal and ambiguous JSON types are rejected.

The registry is stored under `config/storage-assistant/resources.json`. It contains
storage metadata and identifiers, not authentication secrets. Protect the PegaProx
configuration volume and its backups as administrative data.

## Planned execution safety requirements

Future execution-capable releases will not treat a successful definition check as
authorization to modify storage. Before any destructive operation can be enabled,
the implementation must provide all of the following:

- reuse of PegaProx-managed connections without copying credentials into plugin
  files;
- target discovery and independent verification of IQN, LUN, WWID, capacity and
  existing signatures;
- stable device selection by verified identity, never by a transient `/dev/sdX`
  path alone;
- explicit separation between PVE/NFS and PBS/iSCSI workflows;
- one-LUN/one-owner enforcement across all configured resources;
- dry-run planning followed by explicit apply, verify and rollback phases;
- typed confirmation for formatting or signature removal;
- background-task progress, cancellation and complete audit records;
- post-operation verification on the selected PVE cluster or PBS instance;
- multipath verification before registering a multipath-backed datastore.

Formatting, signature removal and remote registration remain out of scope until
these invariants are implemented and tested against disposable lab storage.

## Deployment guidance

PegaProx plugins run inside the PegaProx process and must be reviewed as privileged
server code. Pin a specific release, inspect changes before upgrading and test the
plugin in a non-production environment before connecting it to real infrastructure.

NAS-side initiator ACLs, CHAP, network segmentation and read/write export policy
remain the administrator's responsibility and are not replaced by plugin-side
validation.
