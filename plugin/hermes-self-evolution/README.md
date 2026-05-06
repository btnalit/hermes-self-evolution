# hermes-self-evolution

Runtime context bridge for the **self-evolution-governor** — an optional
Hermes Agent plugin that ensures ``runtime_digest.md`` is monitored and
available for injection into every agent session.

## How it works

The self-evolution-governor is a cron-managed process that periodically
produces a ``runtime_digest.md`` file at
``$HERMES_HOME/state/evolution/runtime_digest.md``.  This digest contains
the agent's recent evolution state — insights, patterns, priorities, and
self-improvement context.

This plugin does **two** things:

| Component | What it does |
|-----------|--------------|
| ``on_session_start`` hook | Reads the digest file and logs its presence, size, and expiry status so the operator can verify the bridge is operational. Gracefully handles missing files and parse errors — never crashes. |
| ``SKILL.md`` instructions | The actual digest content is injected into the agent's system prompt by the skill's own instruction file, which the Hermes plugin system loads automatically on every session. |

The split is intentional: **this plugin is purely the initialization hook**
that logs state and catches issues early (expired digest, missing file,
permission errors).  The injection itself is driven by ``SKILL.md``, which
tells the agent to read ``runtime_digest.md`` at the start of each session.

## Installation

1. Copy the plugin directory into your Hermes Agent plugins path:

   ```bash
   cp -r hermes-self-evolution ~/.hermes/hermes-agent/plugins/
   ```

2. Ensure the plugin is loaded — check your Hermes config or add it to
   the enabled plugins list:

   ```yaml
   # config.yaml
   plugins:
     enabled:
       - hermes-self-evolution
   ```

3. Verify the hook registered successfully:

   ```bash
   hermes --debug --eval 'say "hello"'
   # Look for: [self-evolution] runtime_digest.md loaded ...
   ```

## Prerequisites

- The **self-evolution-governor** cron job must be running to produce
  ``runtime_digest.md``.  Without the digest file, the plugin logs a
  debug message and proceeds normally (no crash, no error).
- The **hermes-self-evolution** SKILL.md must be installed and active
  for the actual content injection to work.

## Log messages

| Level | Message | Meaning |
|-------|---------|---------|
| INFO | ``runtime_digest.md loaded (N bytes)`` | Digest is present and being monitored — normal operation. |
| WARN | ``runtime_digest.md has expired`` | The digest's ``Valid until:`` timestamp is in the past. Run the governor to refresh. |
| WARN | ``Cannot read runtime_digest.md: <error>`` | File exists but is unreadable (permissions, lock, corrupted). |
| DEBUG | ``runtime_digest.md not found`` | File does not exist. The governor may not be running yet. |
| DEBUG | ``HERMES_HOME not set`` | Environment issue — plugin cannot locate the digest. |

## Files

```
hermes-self-evolution/
├── plugin.yaml      # Plugin metadata and hook declarations
├── __init__.py      # on_session_start hook implementation
└── README.md        # This file
```
