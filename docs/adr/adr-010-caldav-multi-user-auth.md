# ADR-010: CalDAV Multi-User Authentication Strategy

## Status

Draft

## Context

We need to support external calendar applications (e.g., Thunderbird, iOS Calendar) connecting to Genesis via CalDAV. Each user should have their own CalDAV account so they can sync their tasks and projects independently.

We have a local Radicale server that stores all CalDAV data. We need to decide:

1. How credentials work between Genesis and Radicale
2. How users are created on Radicale
3. When authentication to external clients is handled

## Decision

### 1. Per-User Radicale Accounts

Each Genesis user has their own CalDAV account on the Radicale server. This allows:

- Isolation of user data (each user sees only their calendars)
- Standard CalDAV authentication for external clients
- Future extensibility (e.g., sharing calendars between users)

User accounts are created on-demand when a user first accesses the system (lazy creation).

### 2. Shared Internal Credentials

The connection between Genesis (Python code) and the local Radicale server uses a shared default password:

```
DEFAULT_RADICALE_PASSWORD = "genesis-local"
```

This is defined in `src/genesis/dav/server.py` as a Python constant.

**Rationale:**
- Radicale runs locally and is not exposed to the network
- Authentication to external clients is handled by the per-user credentials in htpasswd
- Genesis-level authentication will be added separately (see Future Work)

### 3. On-Demand User Creation

When a user (e.g., `alice`) first runs a command like `genesis --user alice task list`:

1. The system checks if `alice` exists in the Radicale htpasswd file
2. If not found, `alice` is added with the default password
3. Alice's CalDAV principal and default calendars are created if needed
4. The operation proceeds with CRUD

This ensures zero-configuration for new users while maintaining proper isolation.

### 4. htpasswd Management

The htpasswd file is stored at `{data_path}/users` and contains entries like:

```
alice:genesis-local
bob:genesis-local
```

Radicale re-reads this file on each authentication request, so adding users does not require restarting the server.

## Consequences

### Positive
- Users can immediately use external CalDAV clients with their Genesis credentials
- No manual user provisioning required
- Simple internal architecture (shared credentials to Radicale)
- Data isolation per user

### Negative
- All auto-created users share the same default CalDAV password
- Users cannot set custom CalDAV passwords yet (future enhancement)

## Future Work

### External Authentication Proxy
When we add HTTP authentication for external clients, we will:
- Add a reverse proxy (e.g., nginx, traefik) in front of Radicale
- Configure the proxy to authenticate requests against a database or external IdP
- Users will authenticate at the proxy level, not directly to Radicale

### Per-User CalDAV Passwords
Allow users to set custom CalDAV passwords:
```bash
genesis --user alice user set-caldav-password --new xK9$mN2...
```

### User Management CLI
Commands for administrators:
```bash
genesis user create alice          # Create user with CalDAV account
genesis user list                  # List all users
genesis user reset-caldav alice     # Regenerate CalDAV password
genesis user delete alice          # Remove user and CalDAV account
```

## References

- [ADR-009: Radicale + CalDAV Server](./adr-009-radicale-caldav-server.md)
- [Radicale htpasswd authentication](https://radicale.org/v3.html#authentication)
