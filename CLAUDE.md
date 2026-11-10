# Working rules for Claude Code — Maison des Fraises repositories

This repository is part of the Home Assistant platform of the Maison des Fraises (HAOS on a ZOTAC ZBOX, local network).
Two kinds of Claude sessions work on it. These rules prevent them from overwriting each other.

## Who owns what

| Scope | Owner |
|---|---|
| Code in this repository: Python integrations, dashboard cards (JavaScript), tests, CI, README, CHANGELOG | **Claude Code** (this session type), on a branch, through a pull request |
| Everything stored inside Home Assistant: automations, scripts, helpers, dashboards, integration entries, add-ons, backups, restarts | **Projet HA session** (claude.ai project "Home Assistant") |
| Deployment of a released version to Home Assistant, post-deployment checks, operations journal | **Projet HA session** |
| Homey (kept in parallel until the end of the migration) | **Projet HA session**; `homey-*` repositories are history, read-only unless Stéphane asks |

Claude Code has no access to the running Home Assistant. It never deploys, restarts or edits anything on the server.

## Source of truth

- `main` is the only source of truth for the code. What runs on Home Assistant must be a commit of `main`, recorded in `DEPLOYED.md`.
- No direct fix on the server. If an urgent fix has to be made on the server, the Projet HA session pushes it the same day to a `hotfix/<topic>` branch and opens a pull request; Claude Code rebases on it.
- Before starting work: `git fetch`, rebase on `origin/main`, and list open pull requests and branches (`gh api repos/{owner}/{repo}/pulls`). If an open pull request touches the same files, build on it or wait; never start a parallel rewrite.
- One active Claude Code session per repository at a time.

## How work flows

1. **Requests.** Stéphane asks directly, or the Projet HA session opens a GitHub issue labelled `from-ha` with the context (entity_ids, logs, expected behaviour). Questions back to the Projet HA session go in the issue or pull request.
2. **Branch** `cc/<short-topic>` from `main`. Small, focused commits, English messages.
3. **Pull request** to `main` with:
   - what changes and why, in plain words;
   - the version bump (`manifest.json` / card header) and a `CHANGELOG.md` entry;
   - a **"Home Assistant impact"** section: new, renamed or removed entities, services, options or card keys, and the configuration changes the Projet HA session must make at deployment. Write "none" if none.
4. **CI green** (ruff + pytest, or the card syntax checks) before asking for review. Stéphane merges.
5. **Deployment** by the Projet HA session only: backup, deploy the merged commit, verify checksums, restart or reload if needed, record it in the project journal.
6. **Deployment record.** The Projet HA session then updates `DEPLOYED.md` (version, commit, date) on `main` and closes the issue. Claude Code never edits `DEPLOYED.md`. (Tags and releases cannot be created from Claude sessions; `DEPLOYED.md` replaces them.)

## Public interface — do not break silently

`entity_id`s, unique_ids, service names and fields, config entry options, and card configuration keys are used by automations and dashboards that live in Home Assistant, not in this repository.
- Never rename or remove one without listing it under "Home Assistant impact" with the migration to apply.
- Prefer adding over renaming; keep backward compatibility for one release when possible.

## Behaviour rules of the platform (apply to any decision logic)

- A new strategy starts in `dry_run` ("decides without moving") and acts only after Stéphane validates its decisions.
- Every automatic decision is explainable in one sentence and logged.
- A manual command suspends the strategy on that device. Hysteresis on every threshold.
- A missing sensor degrades to a simple rule, never to a blind action.
- Presence comes from the phones first; cars are only a confirmation. Actions take presence and the "house open" state into account.
- Never command shutters, the garage door or the gate automatically; the gate is never opened automatically.

## Confidentiality

- Never commit a token, password, network key, API key, serial number, full MAC address, public IP, GPS coordinates or a remote-access URL. Use `secrets.yaml` references, config-flow fields or placeholders.
- Repository content is in English. Conversations with Stéphane are in French, concise.

## Checks before opening a pull request

- `ruff check .` and `pytest -q` (integrations), or `node --check` on every card file (cards).
- No secret or personal data in the diff (`git diff origin/main | grep -iE "token|password|apikey|secret"` must be empty of real values).
