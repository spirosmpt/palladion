# Architecture

The core knows nothing about Mati. A **scenario** (`scenarios/<id>/`) supplies the area, the times, the fire's
sourced points, wind, units and messages; the server runs it on a virtual clock that the desk can play, pause,
speed up or jump.

The rule throughout: **agents judge and write, code computes and checks, a person decides.**

## Per message

1. **Mask** phones, e-mails, tax and social-security numbers, IDs, IBANs, cards and plates before any model
   reads the text. Lengths are kept so quotes still line up with the original the operator sees.
2. **Intake** (agent) extracts place, people, vulnerabilities, situation and what they ask for. Every field
   carries an exact quote; a quote not found in the message is dropped.
3. **Critic** (agent, only on doubt: several numbers, "fine" next to "help", rumours, orders to the system)
   objects with quotes; intake revises once.
4. **Locate**: code matches streets, areas and landmarks (Greek, Greeklish, English). If that fails, the
   **location agent** searches with up to 3 map tool calls and may only pick from what the tools returned.
5. **Hazard critic** (agent, when the proposal moves people and the front is near) warns from facts code computed
   (minutes to the front, a route with margin, trapped, cliff) and may suggest a safer decision from a fixed table.
6. **Credibility** (agent, on doubt signals) can flag "needs confirmation"; it never drops a message.
7. **Needs** (rules, plus the agent when health is mentioned) sets a medical deadline: 15′ critical, 45′ serious.
8. **Arbiter and decision** (code): a fixed table settles disagreements between agents; the decision is one of
   `EVACUATE_ROUTE_CANDIDATE`, `SHELTER_CANDIDATE`, `SEA_RESCUE_CANDIDATE`, `DISPATCH_UNIT_CANDIDATE`, `CALLBACK`,
   `REVIEW_REQUIRED`, `MISSING_PERSON`, `REPORTED_SAFE`, `MONITOR`, `INFO`, `OUT_OF_AREA`.
9. When needed: **duplicates** (agent compares with up to 3 candidates code picked), **call-back** questions
   (up to 3, only for what is missing), a **contradiction** question, and the **Iris** second opinion.
10. The case enters the queue. Nothing executes until an operator acts; the server refuses an action taken on a
    stale version of the case.

## Desk agents

| Agent | Writes | Code checks |
|---|---|---|
| 112 writer | Cell-broadcast text, Greek and English | area, instruction and roads come from code; length; no other places |
| Coast Guard liaison | One request per stretch of shore, ranked (tools: messages, shore, fire, warnings sent) | groups from code; every number must be in the data |
| Crew brief | The card for a dispatched unit | numbers and address must match; otherwise a template |
| Field report reader | What a crew reported (rescued, still there, nobody, new hazard, status) | exact quotes, no negations or maybes; code applies the outcome |
| Supervisor | Up to 3 alerts every 10 scenario minutes, with why now | findings, owner, deadline and escalation from code; actions need acceptance |
| Shift handover | Up to 6 sentences | each sentence cites code-built lines and only their numbers |

Outside the app, a **red-team attacker** agent writes misleading messages in 15 categories; it declares what it
tests before the desk sees the message, and only code judges the result.

**Oumi Iris** answers three questions per reading (which action, immediate danger to life, genuine or not). Code
compares it with the proposal (agrees / disagrees / unsure) and raises flags; it never changes the proposal.

## Engines (no model)

| Module | Does |
|---|---|
| `engines/fire` | Elliptical front through the sourced points, confined to the Copernicus perimeter: arrival time anywhere, burned area, isochrones |
| `engines/routing` | OSM road graph with a closing time per segment, dead ends, evacuation routing with a 10′ margin |
| `engines/buildings.py`, `engines/coast.py` | Arrival per building; cliffs and shores with access to the water |
| `engines/warnings.py` | 112 areas by settlement and the instruction for each |
| `engines/missions.py` | First-arriving unit, routes only before or after the front, veto with alternatives |
| `engines/registry` | Append-only, hash-chained event log; some event types only a human may write |
| `cases/contradictions.py` | One claim against one other source, both quoted |
| `i18n.py` | Code-written Greek to English with fixed rules, when the client sends `X-Lang: en` |

## Model client

`llm/client.py` is the only way agents reach a model. Providers: Vertex AI (Application Default Credentials),
Gemini API (key), Replay (cached answers only) and Scripted (tests). Every answer is validated with Pydantic,
cached by a hash of its input, counted against a daily budget and logged with role, model, latency and tokens.
Each agent's start and end are reported live to the desk and the public page.

## Front end

React, TypeScript, TanStack Query, Zustand and MapLibre. The server streams the clock over SSE. Screens: the
desk (map, time bar, side panel with Cases, 112, Watch and Map tabs), the agents view (per message and for the
desk agents, with timings), the full-screen card for a new public message, and the public page.

## Security

- Personal data is masked before any model call.
- Instructions inside a message are never followed; such a case goes to human review.
- Hosted, the desk needs the room key; the public page sees only its own message (a per-message token) and is
  rate limited (3 a minute per sender, 60 per ten minutes overall).
