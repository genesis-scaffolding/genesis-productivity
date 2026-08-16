# 003. Environment Variable Naming

## Context

Environment variables are the highest-precedence config layer. They must be namespaced to avoid collisions with other tools, and the naming scheme must support nested config sections.

## Decision

We will use double underscore as the separator. Format:

```
<APP_NAME>__<SECTION>__<KEY>
```

APP_NAME is uppercase of the configured app name. SECTION and KEY are uppercase.

Examples:
```
MYPROJECT__APP_NAME
MYPROJECT__PATHS__STATE
MYPROJECT__DATABASE__HOST
```

Double underscore is unambiguous — it does not conflict with single-underscore conventions used by most tools.

## Consequences

- All env vars are namespaced. No collision with system or tool env vars.
- Nested config sections map naturally to the naming scheme.
- Standard Python dotenv libraries can read these directly.
