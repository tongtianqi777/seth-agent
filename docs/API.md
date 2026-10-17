# HTTP API

This is the reference for apps and tools that read the school calendar from seth-agent. As the agent starts maintaining more kinds of school information, they will be served here too. To set up and run the agent, see the [README](../README.md). To understand how the data is stored, see [MEMORY.md](MEMORY.md).

## Basics

- **Base URL:** `http://127.0.0.1:8765`. Change it with `SETH_API_HOST` and `SETH_API_PORT`.
- **Availability:** the API is served while `seth-agent run` is running.
- **Access:** it has no authentication, and apart from searching photos and asking for slideshow videos it only reads. It listens on localhost only, so only programs on the same machine can reach it.
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
| [`POST /media/search`](#post-mediasearch) | Find photos by describing them, optionally within one group |
| [`GET /media/{id}`](#get-mediaid) | An indexed photo's bytes |
| [`POST /slideshows`](#post-slideshows) | Start making a slideshow video of an event |
| [`GET /slideshows`](#get-slideshows), [`GET /slideshows/{id}`](#get-slideshows) | The videos being made and finished, and how each is getting on |
| [`GET /slideshows/{id}/video`](#get-slideshowsidvideo) | A finished video, to play or download |
| [`GET /health`](#get-health) | Whether the agent is keeping up |
| [`GET /metrics`](#get-metrics) | Counts for a status page: what the agent has done, and the photo index |

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

## `POST /media/search`

Finds photos by describing what they show. The group is a separate field, not part of the description: with `group`, only photos sent in that group are considered, and they are ranked by how well they match `query`. Without it, every group's photos are searched.

Photo search needs the optional `media` install (`pip install -e '.[media]'`, see the [README](../README.md#photo-search)). Without it, this endpoint answers `503`.

**Request body** (JSON)

| Field | Default | Description |
|---|---|---|
| `query` | required | What the photos show, in plain words: `"children on a soccer field"`. Not blank |
| `group` | none | A group's exact name, ignoring case and extra spaces (`"Preschool"`). Its media folder name or JID also work. Omit it, or send `null`, to search every group |
| `limit` | `20` | How many photos to return, 1 to 100 |
| `min_score` | none | Leave out photos that match less well than this |

```bash
curl -s http://127.0.0.1:8765/media/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "children sitting at table", "group": "Preschool"}'
```

```json
{
  "query": "children sitting at table",
  "group": { "name": "Preschool", "jid": "120363953379380360@g.us" },
  "searched": 412,
  "results": [
    {
      "file_id": 41,
      "score": 0.3127,
      "group": "Preschool",
      "chat_jid": "120363953379380360@g.us",
      "message_id": "3A6FD07C4B2A11E0D93A",
      "sender": "Melissa Chen",
      "sent_at": "2026-10-14T16:04:00Z",
      "caption": "Snack time!",
      "mime": "image/jpeg",
      "url": "/media/41"
    }
  ]
}
```

| Field | Description |
|---|---|
| `group` | The group that was searched, or `null` when none was given |
| `searched` | How many indexed photos were compared. It is `0` for a group with no indexed photos yet |
| `results[].score` | How well the photo matches, from -1 to 1, best first. The numbers are small even for good matches (a good one is typically 0.25 to 0.35), so compare scores within one search rather than against a fixed bar |
| `results[].caption` | The photo's caption, or the message's text for an album. May be `null` |
| `results[].url` | Where to get the photo: [`GET /media/{id}`](#get-mediaid) on this server |

A search always returns the best `limit` photos it has, even if none is a good match. Use `min_score` to cut those off. An empty `results` means there is nothing indexed to compare.

**Errors:** `404` if `group` isn't the exact name of a group (the message lists the known ones). It never guesses: "Preschool" does not match "Preschool P.E. Teachers". `422` for a blank `query` or a `limit` out of range. `502` if seth-feed can't be reached to look up the group. `503` if photo search isn't installed or is turned off.

## `GET /media/{id}`

The photo with this seth-feed file id, as raw bytes with its `Content-Type`, fetched from seth-feed. Only photos that appear in search results are served.

**Errors:** `404` if the photo isn't indexed, or seth-feed no longer has it. `502` if seth-feed can't be reached.

## `POST /slideshows`

Starts a slideshow video of an event, made like `seth-agent slideshow --find` (see the [README](../README.md#slideshow-videos)): the model reads the sentence into the event's name, date and place, photo search finds its photos (around its date, when it has one), and the video is made in the school's branding. A video takes a minute or two, so this answers at once with a job, and you follow it with [`GET /slideshows/{id}`](#get-slideshows).

Needs photo search (see [`POST /media/search`](#post-mediasearch)) and a model to read the sentence.

```bash
curl -s http://127.0.0.1:8765/slideshows \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Christmas Program on 12/12/2026 at school"}'
```

```json
{
  "id": "597c1f559138",
  "prompt": "Christmas Program on 12/12/2026 at school",
  "status": "queued",
  "created_at": "2026-12-14T22:38:43Z",
  "finished_at": null,
  "error": null,
  "title": null, "event_date": null, "at_school": null,
  "queries": [], "window": null, "photos": [], "seconds": null,
  "video_url": null
}
```

**Request body** (JSON): `prompt`, the event in a sentence, 1 to 300 characters. Leave the date out to look through every photo.

**Errors:** `422` for a blank or too long `prompt`. `429` when three videos are already waiting or being made. `503` when photo search or slideshows aren't set up.

## `GET /slideshows`

The most recent ten jobs, newest first, with whether slideshows are available: `{"enabled": true, "reason": null, "jobs": [...]}`. When `enabled` is `false`, `reason` says why (and `jobs` is empty).

`GET /slideshows/{id}` returns one job. Videos are made one at a time, in the order they were asked for. A job's fields:

| Field | Description |
|---|---|
| `status` | `queued`, `running`, `done` or `failed` |
| `error` | Why it failed, in words for a person, when `status` is `failed` |
| `title`, `event_date`, `at_school` | What the model understood from the sentence (once the video is made) |
| `queries` | What it looked for in the photos |
| `window` | The two days (`YYYY-MM-DD`, both included) the photos were taken from, when the sentence had a date, otherwise `null` |
| `photos[]` | The photos in the video, in the order shown: `file_id` (see [`GET /media/{id}`](#get-mediaid)), `group`, `sent_at` |
| `seconds` | How long the video runs |
| `video_url` | Where to get the video, when `status` is `done` |

Jobs are kept in memory: the newest ten stay (an older one is deleted with its video file), and all are forgotten when the agent restarts. **Errors:** `404` if there is no such job.

## `GET /slideshows/{id}/video`

The finished video, an MP4 (`video/mp4`) that supports seeking. It plays in a browser; add `?download=true` to make the browser save it as `<title>.mp4` instead.

**Errors:** `404` if there is no such job, `409` if the video isn't ready yet (or its file is gone).

## `GET /health`

Tells you whether the agent is keeping up. Use it for a status light, or to check the agent before you trust the calendar.

```json
{
  "last_poll_at": "2026-10-12T23:45:59Z",
  "source_cursor": "43.2",
  "last_transient_error": null,
  "last_extraction_error": null,
  "active_events": 3,
  "active_volunteering_events": 2,
  "indexed_photos": 412
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
| `indexed_photos` | How many photos can be found with `/media/search` |

## `GET /metrics`

Counts that describe what the agent has done so far. The dashboard's Status tab is drawn from this. Unlike the calendar, these are about the agent itself, so they are a good fit for a status page or an alert. For "is it running right now?" use [`/health`](#get-health).

```bash
curl -s http://127.0.0.1:8765/metrics
```

```json
{
  "started_at": "2026-10-16T22:57:54Z",
  "agent": {
    "extractors": {
      "calendar": { "extracted": 118, "skipped": 903 },
      "volunteering": { "extracted": 40, "skipped": 981 },
      "media": { "extracted": 205, "skipped": 816 }
    },
    "failed_batches": 0,
    "events": { "school": { "active": 12, "cancelled": 2 }, "volunteering": { "active": 9 } }
  },
  "photos": {
    "enabled": true,
    "model": "openai/clip-vit-base-patch16",
    "searchable": 708,
    "indexed": 708,
    "unique_images": 696,
    "embedding_bytes": 1449984,
    "oldest_sent_at": "2026-10-01T16:00:00Z",
    "newest_sent_at": "2026-10-14T16:00:00Z",
    "last_indexed_at": "2026-10-16T22:57:54Z",
    "by_model": [ { "model": "openai/clip-vit-base-patch16", "photos": 708, "dimensions": 512 } ],
    "by_group": [ { "group": "Preschool", "photos": 412 }, { "group": "Kindergarten", "photos": 230 } ],
    "searches": { "count": 14, "avg_ms": 38.2, "last_ms": 41.0, "last_at": "2026-10-16T23:10:02Z" }
  }
}
```

| Field | Description |
|---|---|
| `started_at` | When this API process started |
| `agent.extractors` | For each extractor (`calendar`, `volunteering`, `media`), how many messages it has handled, by outcome: `extracted` (read), `skipped` (not for it, e.g. another group's) and `failed` (given up on). Outcomes with no messages are left out. Totals over the life of the memory database |
| `agent.failed_batches` | Batches given up on, as recorded in `extraction_errors`. `/health` has the latest one |
| `agent.events` | Events by calendar (`school`, `volunteering`) and `status` (`active`, `cancelled`). Missing keys mean zero |
| `photos.enabled` | Whether photo search is installed and on. When `false`, `/media/search` answers `503` and `model` is `null` |
| `photos.model` | The embedding model in use (`SETH_MEDIA_MODEL`) |
| `photos.searchable` | Photos indexed by the model in use. These are the ones a search can return |
| `photos.indexed` | All photos in the index. More than `searchable` when some were indexed by a model you've since changed |
| `photos.unique_images` | Distinct photos by content. A photo forwarded to another group counts once here and twice in `indexed` |
| `photos.embedding_bytes` | Storage the embeddings take in the memory database |
| `photos.oldest_sent_at`, `newest_sent_at` | The sent times of the oldest and newest indexed photos. `null` when there are none |
| `photos.last_indexed_at` | When a photo was last indexed. `null` when there are none |
| `photos.by_model` | Photos and vector size (`dimensions`) for each model that made embeddings |
| `photos.by_group` | Indexed photos per group, most first. The name is the group's name when each photo was indexed |
| `photos.searches` | Photo searches answered since the API started: `count`, average and latest time in milliseconds, and when the latest was. `null` until there has been one. These reset when the agent restarts |

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
