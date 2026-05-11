# GitHub Secrets used by this repo

Defined under: **Repo Settings → Secrets and variables → Actions**.

| Secret | Used in | Purpose |
|--------|---------|---------|
| `TG_BOT_TOKEN` | `release.yml` | Bot that posts release-notifications to the operator's Telegram. Optional — workflow no-ops if unset. |
| `TG_CHAT_ID`   | `release.yml` | Chat ID to receive release notifications. |

This file is **only documentation** — no secret values live here. If you fork
this repo and want notifications, add the secrets in your fork's settings.
