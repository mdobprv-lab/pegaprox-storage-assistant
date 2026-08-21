# PegaProx Storage Assistant

[![CI](https://github.com/mdobprv-lab/pegaprox-storage-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/mdobprv-lab/pegaprox-storage-assistant/actions/workflows/ci.yml)

> [!IMPORTANT]
> **Early community preview.** Version 0.2.0 validates and stores storage
> definitions and adds read-only inspection through PegaProx-managed connections.
> It never logs in to iSCSI targets, mounts, formats or registers remote storage.
> Remote mutation is deliberately disabled.

PegaProx 1.0.2+ plugin for two deliberately separated storage workflows:

- **PVE / NFS** — cluster-wide technical content (`iso`, `vztmpl`, `snippets`, `import`), never VM disks or backups.
- **PBS / iSCSI** — a dedicated LUN attached to one PBS, formatted as XFS/ext4 and registered as a PBS datastore.

The registry supports multiple NAS devices, exports, targets, LUNs and network
locations. Distinct portals exposing the same WWID will be treated as paths to one
LUN in the execution milestone.

## Design boundaries

| Managed system | Protocol | Intended role | Explicitly excluded |
|---|---|---|---|
| PVE cluster | NFS | Shared technical content: `iso`, `vztmpl`, `snippets`, `import` | VM disks and backups |
| PBS server | iSCSI | Dedicated LUN formatted with XFS/ext4 and registered as one PBS datastore | Attaching the same LUN to PVE or another PBS |

The separation is intentional. PVE receives cluster-wide NFS storage for technical
content, while PBS receives a block device with one unambiguous owner. The plugin is
NAS-vendor-neutral and is designed to manage definitions from multiple NAS devices
and network locations.

## Current milestone: 0.2.0 read-only discovery preview

This foundation release provides:

- PegaProx manifest, backend registration and embedded plugin page;
- English and Polish UI catalogs with per-key English fallback;
- Modern, Corporate Dark/Light and Cloud Dark/Light presentation modes;
- automatic inheritance of the signed-in user's PegaProx language with English fallback;
- plugin-wide fallback language and theme defaults through PegaProx's `config.json` editor;
- two-step NFS and iSCSI definition wizards with review pages;
- complete safety review of every destination-critical NFS and iSCSI field;
- user-friendly NFS export input normalized to one absolute POSIX path;
- persistent, cross-process locked multi-resource registry;
- separate create/update operations with stale-ID and resource-type overwrite protection;
- strict resource validation and a read-only deployment-plan endpoint;
- hard allowlist for PVE technical content;
- WWID/LUN/datastore/export ownership collision prevention;
- RBAC and PegaProx audit logging for definition changes;
- object-level access checks before definitions or discovery can reference PVE/PBS;
- token-scoped authorization through PegaProx `build_authz_user`;
- a narrow compatibility adapter around PegaProx-managed PVE/PBS connections;
- read-only PVE NFS export inspection from selected cluster nodes;
- read-only PBS inspection of disks, datastores, existing iSCSI sessions,
  block devices and multipath maps;
- bounded background discovery tasks on named daemon workers, with progress,
  cooperative cancellation, per-user result ownership and audit events;
- native, cluster-scoped PegaProx SSE progress for PVE, with authenticated REST
  polling as a compatibility fallback;
- no remote mutation yet (`execution_enabled: false`).

### Compatibility and validation

- tested with PegaProx 1.0.2 in the official Docker deployment model;
- tested on Python 3.12;
- verified in Modern, Corporate Dark/Light and Cloud Dark/Light layouts;
- English and Polish interface catalogs with matching keys;
- 70 automated tests covering validation, persistence, concurrency, authorization,
  compatibility boundaries, discovery parsers, task ownership and frontend contracts;
- CI validates Python, JSON, shell scripts, embedded JavaScript and the complete
  unit-test suite.

Real NAS, PVE and PBS tests must still be performed in a disposable lab before any
execution-capable milestone is considered.

## Screenshots

All storage definitions shown below are synthetic examples. Host addresses use the
RFC 5737 documentation range `192.0.2.0/24`; the IQN, WWID and datastore names do
not identify real infrastructure. The `10.220.0.20` and `10.221.0.20` portal
addresses visible in the empty iSCSI form are static UI placeholders. They are not
stored unless an operator explicitly enters them.

### Resource overview

![Storage Assistant resource overview](docs/images/storage-overview.png)

### PVE/NFS wizard

![PVE NFS definition form](docs/images/pve-nfs-form.png)

![PVE NFS definition review](docs/images/pve-nfs-review.png)

### PBS/iSCSI wizard

![PBS iSCSI definition form](docs/images/pbs-iscsi-form.png)

![PBS iSCSI definition review](docs/images/pbs-iscsi-review.png)

<details>
<summary>Plugin activation in PegaProx 1.0.2</summary>

![Storage Assistant enabled in PegaProx](docs/images/plugin-enabled.png)

</details>

API base: `/api/plugins/storage-assistant/api`

| Route | Methods | Purpose |
|---|---|---|
| `ui` | GET | Plugin page |
| `locale?lang=en|pl` | GET | Translation catalog |
| `settings` | GET | Sanitized, non-sensitive UI defaults |
| `status` | GET | Plugin and registry summary |
| `resources` | GET, POST, PUT, DELETE | List, create, update or delete validated definitions |
| `plan` | POST | Generate a non-executing deployment plan |
| `discovery` | GET, POST, DELETE | Start, poll or cancel a read-only inspection task |

The generic PegaProx plugin editor exposes `config.json`. Storage Assistant accepts only:

```json
{
  "default_language": "auto",
  "theme_override": "auto"
}
```

The normal UI language is inherited from the signed-in user's PegaProx preference;
the plugin does not expose a second language selector. `default_language` accepts
`auto`, `en` or `pl` and is used only as a compatibility fallback when PegaProx does
not expose a language preference. `theme_override` accepts `auto`,
`modern-dark`, `corporate-dark`, `corporate-light`, `cloud-dark` or `cloud-light`.
Unknown values and keys are ignored by the runtime API. Remote execution cannot be
enabled through this file.

In version 0.2.0, **Validate definition** checks syntax, normalization, ownership
collisions and safety classification only. It does not probe NAS, PVE or PBS.
The separate **Inspect** action performs read-only observation of a saved definition.

PVE inspection uses `check_cluster_access(cluster_id)` before resolving the manager,
then reads cluster nodes, configured storage and the NFS export list visible from
selected online nodes. It does not mount the export and does not perform a write test.

PBS inspection requires `pbs.view`, `pbs.disks.view` and `pbs.datastore.view`, runs
`check_pbs_access(pbs_id)` before resolving the manager, and reads PBS disk/datastore
APIs plus already-established iSCSI, block-device and multipath state through a
hard-coded read-only command allowlist. It does not run SendTargets discovery, log
in or out, mount a device, change multipath, create a filesystem or register a
datastore. `pbs.disks.smart` is not requested because SMART data is not read.

PVE progress is published through PegaProx's `broadcast_sse(...)` convention and
consumed with a short-lived token from `/api/sse/token`. The event contains only a
task UUID, state and percentage; the detailed result still comes from the
owner-scoped plugin API. PegaProx 1.0.2 scopes SSE subscriptions by PVE cluster but
does not expose an equivalent PBS- or user-scoped channel. PBS progress therefore
uses the same owner-scoped REST polling fallback instead of broadcasting a global
event. This boundary can be replaced by a core PBS SSE scope when PegaProx provides
one.

## Feedback requested

This preview remains published early to validate integration decisions before the
remote execution layer is implemented. The current adapter follows maintainer
guidance from [PegaProx issue #718](https://github.com/PegaProx/project-pegaprox/issues/718):

- authorize PVE/PBS object access before manager resolution;
- reuse PegaProx-managed connections without copying credentials;
- isolate private PVE/PBS API or SSH helpers behind feature detection;
- use existing read permissions for inspection;
- run long inspection work on bounded daemon threads, publish PVE progress through
  PegaProx SSE and keep cooperative cancellation checkpoints;
- avoid global PBS progress broadcasts until PegaProx exposes a PBS- or
  user-scoped SSE target;
- wait for the planned core-owned `pbs.disks.manage` permission before any
  destructive PBS disk action;
- expectations for community-plugin packaging and marketplace readiness.

Please treat the current API and data schema as preview interfaces until the first
execution-capable release.

## Roadmap

1. Resource registry and i18n foundation. *(complete)*
2. Read-only PVE/NFS discovery and cluster visibility checks. *(preview complete)*
3. Read-only PBS/iSCSI observation, WWID and multipath checks. *(preview complete)*
4. Core permission/API alignment, then explicit apply/verify/rollback task engine.
5. Health monitoring and diagnostic reports.

## Development

```bash
python -m unittest discover -s tests -v
```

Do not clone the repository directly under `plugins/`: the repository name and runtime plugin ID intentionally differ. Use the installer so the runtime directory is exactly `plugins/storage-assistant`:

```bash
sudo ./install.sh
# official Docker container, executed inside the container:
PEGAPROX_DIR=/app ./install.sh
```

Rescan plugins and enable **Storage Assistant** in PegaProx settings. Runtime definitions are saved under `config/storage-assistant/resources.json`, not inside the replaceable plugin directory.

Installing into a running Docker container survives a restart but not replacement of
that container. Re-run the installer after recreation, or package/mount
`plugins/storage-assistant` as part of the container deployment. The registry remains
persistent when `/app/config` uses the standard PegaProx volume.

### PegaProx 1.0.2 navigation limitation

Cloud exposes plugin frontends from its global **Plugins** page. Modern and Corporate
insert frontend tabs only into a selected PVE cluster view. PegaProx 1.0.2 does not
provide the same extension point in the PBS view, so use the Storage Assistant tab
from any PVE cluster or open `/api/plugins/storage-assistant/api/ui` directly. This is
a PegaProx navigation limitation; the plugin backend and PBS definitions are global.

## Safety model

One LUN has one role and one PBS owner. Discovery never treats a transient device
path as identity and never turns an observation into authorization for a change.
Formatting will never be inferred from a device path; later execution code will
require target IQN, LUN, WWID, size, signatures, a core-owned destructive PBS
permission and explicit typed confirmation. Real access separation remains the NAS
administrator's responsibility through initiator IQN ACLs, CHAP and network
segmentation.
