# 001. Configuration Management

## Context

The application needs configuration from multiple sources. Users may want to set defaults, override via a config file, or override via environment variables. The system must also support multiple independent instances (e.g., multi-user scenarios).

## Decision

We will use a layered config system with the following precedence (highest to lowest):

1. Environment variables
2. YAML config file (configs.yaml)
3. Internal defaults

The app name defaults to a canonical name in `configs.py`. It can be overridden via environment variable.

## Consequences

- Config always loads, even with no config file present.
- Environment variables are the escape hatch for any setting.
- The same binary can run with different configs by pointing to different YAML files or setting different env vars.
