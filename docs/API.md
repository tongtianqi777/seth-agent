# HTTP API

This is the reference for apps and tools that read the school calendar from seth-agent. As the agent starts maintaining more kinds of school information, they will be served here too. To set up and run the agent, see the [README](../README.md). To understand how the data is stored, see [MEMORY.md](MEMORY.md).

## Basics

- **Base URL:** `http://127.0.0.1:8765`. Change it with `SETH_API_HOST` and `SETH_API_PORT`.
- **Availability:** the API is served while `seth-agent run` is running.
- **Access:** it's read-only and has no authentication. It listens on localhost only, so only programs on the same machine can reach it.
- **Format:** every response is JSON.
- **Dates** are `YYYY-MM-DD` in the agent's local timezone.
- **Timestamps** are RFC3339 in UTC, e.g. `2026-10-12T17:02:00Z`.
- **Dashboard:** the same server shows a web dashboard at `/`. It only uses the endpoints below.
- **Interactive docs:** open `/docs` (Swagger UI) to try the endpoints. The machine-readable schema is at `/openapi.json`.

| Endpoint | Purpose |
|---|---|
| [`GET /calendar`](#get-calendar) | Events in a date range, grouped for display. The school calendar, or a volunteering group's |
| [`GET /volunteering`](#get-volunteering) | The volunteering calendars |
| [`GET /events/{id}`](#get-eventsid) | One event, with the messages behind it and its history |
| [`GET /health`](#get-health) | Whether the agent is keeping up |

## The event object

Every endpoint that returns events uses this shape:

```json
{
  "id": 2,
  "title": "Workout session (rescheduled)",
  "details": "Alex's workout session was moved from October to November.",
  "date_start": "2026-11-01",
  "date_end": "2026-11-30",
  "precision": "month",
  "time": null,
  "status": "active",
  "confidence": 0.85,
  "created_at": "2026-10-12T17:02:31Z",
  "updated_at": "2026-10-12T17:02:31Z",
  "label": "Nov 2026",
  "sources": [
    { "chat_jid": "15555550123@s.whatsapp.net", "message_id": "3A0000000000EXAMPLE1", "role": "created" }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `id` | integer | Stable identifier for the event |
| `title` | string | Short name, e.g. "Dad's flight" |
| `details` | string or null | One sentence of extra context from the messages |
| `date_start` | date | First day the event could be on, inclusive |
| `date_end` | date | Last day the event could be on, inclusive. Equal to `date_start` for single-day events |
| `precision` | string | How exactly the date is known. See [Precision](#precision) |
| `time` | string or null | Time of day if mentioned, as free text: `"15:30"`, `"morning"` |
| `status` | string | `active` or `cancelled` |
| `confidence` | number or null | 0–1. How sure the agent is that this is a real future event with the right date |
| `created_at`, `updated_at` | timestamp | When the agent first recorded the event, and when it last changed it |
| `label` | string | A display key for the date. See [Precision](#precision) |
| `sources` | array | The WhatsApp messages that `created`, `updated` or `cancelled` the event, oldest first |

### Precision

| `precision` | Example message | `date_start` – `date_end` | `label` |
|---|---|---|---|
| `day` | "Dad's flight is on 10/18" | 2026-10-18 – 2026-10-18 | `2026-10-18` |
| `range` | "We're away 10/22 to 10/25" | 2026-10-22 – 2026-10-25 | `2026-10-22..2026-10-25` |
| `week` | "Exams are the week of the 19th" | 2026-10-19 – 2026-10-25 | `Week of 2026-10-19` |
| `month` | "Workout moved to Nov" | 2026-11-01 – 2026-11-30 | `Nov 2026` |
| `year` | "We'll visit sometime next year" | 2027-01-01 – 2027-12-31 | `2027` |

To lay out a calendar, place `day` events on their date and show the others as a band across their span, or group them under their `label`.

## `GET /calendar`

Returns the events whose span **overlaps** a date range. A query for November 10–12 includes a "Nov 2026" event, and a query for October includes a range from Oct 30 to Nov 2.

**Query parameters**

| Parameter | Default | Description |
|---|---|---|
| `from` | today (local) | First date of the range, inclusive |
| `to` | no limit | Last date of the range, inclusive |
| `include_cancelled` | `false` | Also return cancelled events |
| `group` | none | The name of a volunteering group, to read its volunteering calendar instead of the school calendar. Matched ignoring case. See [`GET /volunteering`](#get-volunteering) |

Without `group`, only school events are returned. Volunteering events never appear in the school calendar.

**Response**

| Field | Description |
|---|---|
| `group` | The volunteering group's name, as listed by `/volunteering`. Only present when `group` was asked for |
| `today` | Today's date in the agent's timezone |
| `from`, `to` | The range that was applied. `to` is `null` when there's no upper limit |
| `days` | Events grouped by `label`, in date order. Each entry is a compact summary: `id`, `title`, `details`, `time`, `status`, `precision`, `date_start`, `date_end` |
| `events` | The full [event objects](#the-event-object), ordered by `date_start` |

**Example**

```bash
curl -s 'http://127.0.0.1:8765/calendar?from=2026-10-01&to=2026-11-30'
```

```json
{
  "today": "2026-10-14",
  "from": "2026-10-01",
  "to": "2026-11-30",
  "days": {
    "2026-10-18": [
      { "id": 3, "title": "Dad's flight", "details": null, "time": null, "status": "active",
        "precision": "day", "date_start": "2026-10-18", "date_end": "2026-10-18" }
    ],
    "2026-10-19": [
      { "id": 1, "title": "School closed", "details": null, "time": null, "status": "active",
        "precision": "day", "date_start": "2026-10-19", "date_end": "2026-10-19" }
    ],
    "Nov 2026": [
      { "id": 2, "title": "Workout session (rescheduled)", "details": "…", "time": null, "status": "active",
        "precision": "month", "date_start": "2026-11-01", "date_end": "2026-11-30" }
    ]
  },
  "events": [ "… full event objects …" ]
}
```

An empty range returns `"days": {}` and `"events": []`, with status `200`.

**Errors:** `422` if `from` or `to` isn't a valid `YYYY-MM-DD` date. `404` if `group` isn't a known volunteering group.

## `GET /volunteering`

Lists the volunteering calendars, one per volunteering group: the groups the agent is set to treat as volunteering groups (`SETH_VOLUNTEERING_GROUPS`, in that order, so Preschool P.E. Teachers comes first by default), then any other group that still has events. A calendar with no events yet is still listed.

```bash
curl -s http://127.0.0.1:8765/volunteering
```

```json
{ "calendars": ["Preschool P.E. Teachers"] }
```

Pass a name to [`GET /calendar`](#get-calendar) as `group` to read that calendar.

## `GET /events/{id}`

Returns one [event](#the-event-object), cancelled or not. It adds two things to the usual fields:

- each entry in `sources` has a `message` field with the original WhatsApp message (`group`, `sender`, `sent_at`, `type`, `body`), read live from seth-feed. It's `null` if that message no longer exists. If seth-feed is down, the request fails with `502`.
- a `history` array lists every change, oldest first.

```bash
curl -s http://127.0.0.1:8765/events/3
```

```json
{
  "id": 3,
  "title": "Dad's flight",
  "date_start": "2026-10-20",
  "date_end": "2026-10-20",
  "precision": "day",
  "status": "active",
  "label": "2026-10-20",
  "sources": [
    { "chat_jid": "…", "message_id": "M4", "role": "created",
      "message": { "sender": "Alex Doe", "sent_at": "2026-10-12T17:03:00+00:00", "type": "text", "body": "Dad's flight is on 10/18" } },
    { "chat_jid": "…", "message_id": "M7", "role": "updated",
      "message": { "sender": "Alex Doe", "sent_at": "2026-10-12T17:30:00+00:00", "type": "text", "body": "Oh wait, dad's flight got moved to 10/20" } }
  ],
  "history": [
    { "op": "add",    "before_json": null,                       "after_json": { "date_start": "2026-10-18", "…": "…" }, "reason": "Message states dad's flight date.", "at": "2026-10-12T17:03:12Z" },
    { "op": "update", "before_json": { "date_start": "2026-10-18" }, "after_json": { "date_start": "2026-10-20", "…": "…" }, "reason": "Flight was moved to 10/20.", "at": "2026-10-12T17:30:09Z" }
  ]
}
```

| `history[]` field | Description |
|---|---|
| `op` | `add`, `update` or `cancel` |
| `before_json` | The whole event before the change, or `null` for `add` |
| `after_json` | The whole event after the change |
| `reason` | Claude's one-line explanation of why the messages led to this change |
| `at` | When the change was made |

**Errors:** `404` if there is no event with that id.

## `GET /health`

Tells you whether the agent is keeping up. Use it for a status light, or to check the agent before you trust the calendar.

```json
{
  "last_poll_at": "2026-10-12T23:45:59Z",
  "source_cursor": "43.2",
  "last_transient_error": null,
  "last_extraction_error": null,
  "active_events": 3,
  "active_volunteering_events": 2
}
```

| Field | Description |
|---|---|
| `last_poll_at` | When the agent last checked for messages. If this is more than a minute or two old, the poll loop has stopped |
| `source_cursor` | seth-feed's follow cursor for the newest message fully processed (an opaque string such as `43.2`). `null` before the first successful pass |
| `last_transient_error` | The latest problem that will be retried, such as no API key, a rate limit or a network outage, prefixed with when it happened. It isn't cleared when things recover, so compare its time with `last_poll_at` |
| `last_extraction_error` | The latest batch of messages that was given up on (refused or unreadable): `{id, extractor, message_ids, error, at}`, or `null` |
| `active_events` | How many school events are currently active |
| `active_volunteering_events` | How many volunteering events are currently active, across all volunteering calendars |

## Examples for client code

**Python**

```python
import httpx

cal = httpx.get("http://127.0.0.1:8765/calendar", params={"from": "2026-10-01"}).json()
for label, events in cal["days"].items():
    for e in events:
        print(f"{label:<12} {e['title']}")
```

**JavaScript**

```js
const res = await fetch("http://127.0.0.1:8765/calendar?from=2026-10-01");
const { days } = await res.json();
for (const [label, events] of Object.entries(days)) {
  for (const e of events) console.log(label.padEnd(12), e.title);
}
```

## Stability

- Fields may be **added** to responses over time, so clients should ignore fields they don't recognise.
- Existing fields won't be renamed or removed without a note in this document.
- Event ids are stable for as long as the memory database exists. Deleting `data/memory.db` and rebuilding assigns new ids.
