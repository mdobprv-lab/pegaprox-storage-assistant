# PegaProx Storage Assistant

PegaProx 1.0.2+ plugin for two deliberately separated storage workflows:

- **PVE / NFS** — cluster-wide technical content (`iso`, `vztmpl`, `snippets`, `import`), never VM disks or backups.
- **PBS / iSCSI** — a dedicated LUN attached to one PBS, formatted as XFS/ext4 and registered as a PBS datastore.

The registry supports multiple NAS devices, exports, targets, LUNs and network locations. Distinct portals exposing the same WWID will be treated as paths to one LUN in the execution milestone.

## Current milestone: 0.1.0

This foundation release provides:

- PegaProx manifest, backend registration and embedded plugin page;
- English and Polish UI catalogs with per-key English fallback;
- Modern, Corporate Dark/Light and Cloud Dark/Light presentation modes;
- two-step NFS and iSCSI definition wizards with review pages;
- persistent, cross-process locked multi-resource registry;
- strict resource validation and a read-only deployment-plan endpoint;
- hard allowlist for PVE technical content;
- WWID/LUN/datastore/export ownership collision prevention;
- RBAC and PegaProx audit logging for definition changes;
- no remote mutation yet (`execution_enabled: false`).

API base: `/api/plugins/storage-assistant/api`

| Route | Methods | Purpose |
|---|---|---|
| `ui` | GET | Plugin page |
| `locale?lang=en|pl` | GET | Translation catalog |
| `status` | GET | Plugin and registry summary |
| `resources` | GET, POST, DELETE | List, upsert or delete validated definitions |
| `plan` | POST | Generate a non-executing deployment plan |

## Roadmap

1. Resource registry and i18n foundation.
2. PVE/NFS discovery, cluster reachability checks and wizard.
3. PBS/iSCSI discovery, WWID/multipath safety checks and wizard.
4. Explicit apply/verify/rollback task engine and audit log.
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

## Safety model

One LUN has one role and one PBS owner. Formatting will never be inferred from a device path; later execution code will require target IQN, LUN, WWID, size, signatures and an explicit typed confirmation. Real access separation remains the NAS administrator's responsibility through initiator IQN ACLs, CHAP and network segmentation.
