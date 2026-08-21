# Security Policy

## Project status

PegaProx Storage Assistant 0.2.0 is an early community preview. It validates and
stores resource definitions, generates non-executing deployment plans and performs
explicitly read-only inspection through PegaProx-managed connections. It does not
log in to iSCSI targets, mount, format or register remote storage.

Remote execution is deliberately disabled in this release and cannot be enabled
through `config.json` or the plugin UI.

## Supported versions

| Version | Security support | Notes |
|---|---|---|
| Latest 0.2.x | Best effort | Read-only discovery preview; upgrade to the newest patch release before reporting |
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

Version 0.2.0 is designed around the following constraints:

- `execution_enabled` is hard-coded to `false`;
- `config.json` accepts only language and theme preferences;
- CHAP secrets, SSH credentials and PVE/PBS API tokens are not stored in the
  resource registry;
- PVE content types are limited to `iso`, `vztmpl`, `snippets` and `import`;
- resource creation and updates require manager-level authorization;
- definition changes are submitted to the PegaProx audit log;
- discovery start, completion and cancellation requests are audited;
- PVE access is checked before `get_connected_manager` resolves a manager;
- PBS access is checked before the plugin resolves `pbs_managers[pbs_id]`;
- API-token effective roles are honored through `build_authz_user`;
- PVE discovery requires `storage.view`;
- PBS discovery requires `pbs.view`, `pbs.disks.view` and
  `pbs.datastore.view`; `pbs.disks.smart` is not requested;
- discovery task results are owner-scoped and object access is rechecked when
  polling or cancelling a task;
- tasks run on a fixed-size pool of named daemon threads, expire from memory and
  support cooperative cancellation;
- PVE progress uses PegaProx SSE scoped to the already-authorized cluster and
  carries no infrastructure details;
- PBS progress is never emitted as a global SSE event; PegaProx 1.0.2 has no
  PBS/user SSE scope, so the owner-scoped REST task endpoint remains the fallback;
- registry updates use cross-process locking and atomic replacement;
- duplicate NFS destinations, PBS datastores, target/LUN ownership and WWIDs are
  rejected;
- a LUN definition has exactly one PBS owner;
- NFS path traversal and ambiguous JSON types are rejected.

The PegaProx 1.0.2 PVE API and PBS SSH helpers needed for some observations are
private interfaces. All private access is isolated in `compat.py`, feature-detected
and fails closed with controlled errors. No other plugin module may import manager
dictionaries or call private PegaProx API/SSH helpers.

PBS SSH inspection is limited to a fixed command allowlist: existing iSCSI session
reporting, `lsblk` JSON output and existing multipath maps. No user-controlled value
is interpolated into these commands. The plugin does not invoke SendTargets
discovery, iSCSI login/logout, mount/umount, `wipefs`, `mkfs` or PBS datastore
mutation commands.

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
- the core-owned `pbs.disks.manage` permission proposed by the PegaProx maintainer;
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
