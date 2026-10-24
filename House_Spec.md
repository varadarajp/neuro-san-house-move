# House Move Agent — network specification

A multi-agent assistant, built on [Neuro SAN](https://github.com/cognizant-ai-lab/neuro-san), that helps a UK bank's
mortgage customers tell everyone who needs to know that they are moving house: on time, in the right order, and only
with the customer's approval. Synthetic data only.

Runtime name: `hackathon/house_move`
([`registries/hackathon/house_move.hocon`](registries/hackathon/house_move.hocon)).
**14 LLM agents, 27 coded tools and one middleware.** For the full usage write-up, test list and data sources, see
[`docs/examples/hackathon/house_move.md`](docs/examples/hackathon/house_move.md); for a walk-through, see
[`demo/`](demo/).

## The problem

When a mortgage completes, the customer has to tell 15 to 30 organizations that they are moving — councils, DVLA,
energy, water, insurers, schools, the GP, and more. People forget some, find out late, and some of those are legal
duties with real penalties (up to £1,000 for not telling DVLA, a £70 council tax penalty). The bank then carries the
cost of servicing the resulting contacts.

## Design principle: AI only where it adds value

Everything with a deadline, a fee or a legal consequence is **deterministic code**, so it is auditable and cannot be
hallucinated. The **LLM is used only for judgement** a rules engine cannot do. An LLM never invents a deadline, fee or
legal obligation.

| Deterministic code | LLM agents |
|---|---|
| Which obligations apply (25 sourced rules) | Messy bank descriptors → providers and inferred life facts |
| Deadlines in UK working days, per nation | Free-text answers → structured facts |
| Legal flags and penalties | A personal draft for each organization |
| The approval gate and confirmation codes | Reading provider replies |
| What a date change means for each task | Explaining the plan in plain English |
| Audit log, encryption, statement parsing | Edge cases the rules do not cover (letting the old home, joint owners) |

## The two AI moments

1. **Discovery** — from the customer's own 90 days of transactions, the `discovery_agent` finds which organizations
   they pay and infers the life facts that matter for a move (pet insurance ⇒ a pet whose microchip needs updating;
   a nursery payment ⇒ a young child; vehicle tax ⇒ a car and probably a licence). Anything below 70% confidence is
   asked, not assumed. A checklist cannot make these inferences.
2. **Replanning** — when the completion or exchange date changes, `tracker_agent` rebuilds the dated plan with code
   and sorts every task into: already sent and needing a correction, needing rebooking, updated automatically, or
   unchanged. The agent never computes dates itself.

## Agent network

The **`move_coordinator`** is the only agent the customer talks to (the Front Man). It runs the journey
intake → discover → plan → draft → approve → track.

**Pipeline agents** (called in order, each takes a plain inquiry):

- `intake_agent` — starts a standalone case, imports an uploaded statement or listed providers, deletes a case.
- `profile_agent` — what the bank already knows and the facts still missing; resumes a saved case.
- `discovery_agent` — **AI moment 1**: providers and life facts from transactions.
- `obligations_agent` — which organizations must be told (rules engine), plus both councils looked up live.
- `timeline_agent` — a dated task plan in UK working days against completion and exchange.
- `local_services_agent` — GP practices, schools and water companies near the new home.
- `comms_agent` — one ready-to-send draft per task, with `{{placeholders}}` filled in by code at send time.
- `tracker_agent` — **AI moment 2**: approval codes, sending (mock), the bank's direct update, replanning, audit.
- `reply_agent` — reads provider replies; deliberately has no approve or send tools (least privilege).

**AAOSA specialists** (each decides whether a task is theirs, then records how to do it):
`government_agent`, `home_utilities_agent`, `financial_agent`, `health_family_agent`.

## State and sly_data

All case state (profile, discovered providers, obligations, the dated tasks, drafts, outbox, audit log) lives
**server-side** in a SQLite case store and in `sly_data`. The LLM sees only redacted views; coded tools read and
write the real state. The client holds only its signed session token — anything else it sends under those keys is
discarded.

## Coded tools (27)

- **Discovery and profile**: `get_transactions`, `customer_profile_lookup`.
- **Rules and dates**: `obligations_rules`, `council_lookup` (live, postcodes.io), `build_timeline`, `date` helpers.
- **Directories**: `gov_service_directory`, `provider_directory`, `gp_finder` (NHS, live), `school_finder` (DfE),
  `water_supplier_lookup` (Ofwat appointed areas, live), `insurance_quote_api`.
- **Tasks and drafts**: `list_tasks`, `update_tasks`, `letter_templates`, `save_drafts`.
- **Approval, send, replan**: `request_approval`, `confirm_with_code`, `send_notification`, `bank_address_api`,
  `plan_corrections`, `get_audit_log`.
- **Standalone cases**: `create_case`, `import_statement`, `add_manual_providers`, `delete_case`.
- **Replies**: `check_inbox`, `record_reply`.

`CustomerMessageMiddleware` records the customer's own message server-side before each model call, so confirmation
codes are checked against what the customer actually typed, never against words the LLM passes to a tool.

## Entry routes

- **Bank customer** (`C-…`): starts when the mortgage completion date is set. Profile, 90 days of transactions and
  Direct Debit references come from the bank; account and policy numbers are filled in automatically; the bank
  updates its own records directly.
- **Standalone** (`S-…`): anyone moving. A short chat plus a statement upload (CSV/OFX) or a list of providers;
  personal details come from a secure form and are encrypted in SQLite. A guest resumes with their case reference
  and a one-off resume code, and can delete their data on request.

## Data, privacy and security (enforced in code, not the prompt)

- **The server holds the state**; anything the client sends under state keys is discarded.
- **You open only your own case**: signed sessions, ownership checks and unguessable case IDs. A customer ID the LLM
  passes to a tool cannot open another customer's record (a leak the AI evaluations caught, now covered by a test).
- **Personal data stays out of the LLM**: drafts use `{{placeholders}}` filled by code only at send time.
- **Encrypted at rest**: personal details, statements and plans encrypted; keys kept apart from the database.
- **Statements kept out of the chat**: uploaded to the customer's own folder, read once, then deleted.
- **Consent the LLM cannot fake**: approvals, statement imports and deletions happen only when the customer types a
  one-time 6-digit code in their own message; the middleware records it and the tool checks it, so neither the LLM
  nor an injected provider reply can approve. On the app, approval comes from the customer's own signed-in browser,
  tied to the exact wording they read.

These controls are replayed as attacks in the test suite (`test_session_security.py` and others).

## Rules, data and sources

25 obligation rules (21 verified against an official source on 2026-10-18), each with a legal flag, a penalty note,
timing (source-based or a labelled recommendation) and a source URL. Live data: councils and coordinates from
postcodes.io; GP practices from the NHS Organisation Data Service; schools from the Department for Education; water
and sewerage companies from Ofwat's appointed-area maps. UK-wide: England, Wales, Scotland (water billed with
council tax, Scottish bank holidays) and Northern Ireland (domestic rates via Land & Property Services, NI licence
route).

## Running and testing

Enable the network (it is off by default) and run the server, the nsflow UI and the House Move app — see
[`docs/examples/hackathon/house_move.md`](docs/examples/hackathon/house_move.md). Tested by **155 unit tests** and
**12 data-driven integration fixtures** that run the real network against the LLM and check discovery accuracy, the
plan's legal flags and draft quality against written-down ground truth for five synthetic customers.
