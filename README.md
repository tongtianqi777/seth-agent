<p align="center">
  <img src="docs/assets/logo.png" alt="SETH — Christian Education Institute" width="520">
</p>

<h1 align="center">seth-agent</h1>

<p align="center">
  A private assistant that reads your WhatsApp messages and keeps a calendar of what's coming up.
</p>

---

Messages are full of plans. *"The school is off on Monday."* *"My workout got moved to November."* *"Dad's flight is on 10/18."* They're easy to read and easy to forget.

seth-agent reads the messages that [seth-ingestion](../seth-ingestion) captures from WhatsApp, picks out the ones that mention something in the future, and keeps them in a tidy calendar that you or any app can query:

```
2026-10-18   Dad's flight
2026-10-19   School closed
Nov 2026     Workout session (rescheduled)
```

When plans change, the calendar changes with them. *"Dad's flight moved to 10/20"* updates the existing event instead of adding a second one, and *"the party is cancelled"* cancels it.

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Everyday use](#everyday-use)
- [Reading the calendar](#reading-the-calendar)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [More documentation](#more-documentation)

## What it does

| A message like… | becomes… |
|---|---|
| "The school is off on Monday" (sent Mon Oct 12) | **2026-10-19**: School closed |
| "My workout session has been rescheduled to Nov" | **Nov 2026**: Workout session (rescheduled) |
| "Dad's flight is on 10/18" | **2026-10-18**: Dad's flight |
| "Dad's flight got moved to 10/20" | the same event, moved to **2026-10-20** |
| "Hello", "I'm outside waiting for you", "Will you be able to make it?" | nothing, because these aren't future events |
| A photo or video with no caption | nothing (the caption is read when there is one) |

Things worth knowing:

- **Dates are read the way a person would.** "Monday", "next week" and "in Nov" are worked out from when the message was **sent**, so an old message means the same thing today as it did then.
- **Rough dates are fine.** An event can be on a day, a range of days, a week, a month or a year.
- **Every event shows where it came from.** You can see the exact messages behind it and every change made to it.
- **It keeps up on its own.** New messages are picked up within about 15 seconds.

## Quick start

You need:

- macOS with [Homebrew](https://brew.sh) Python 3.11 or newer. The system Python is too old.
- [seth-ingestion](../seth-ingestion) set up and capturing messages.
- An Anthropic API key from the [Claude Console](https://console.anthropic.com).

**1. Install**

```bash
cd seth-agent
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

**2. Add your API key**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

To keep it set, add that line to your `~/.zshrc`.

**3. Start it**

```bash
.venv/bin/seth-agent run
```

The first time it runs, the agent reads every message already captured and builds the calendar from them. After that, it watches for new messages. Leave it running in a terminal tab, and press **Ctrl+C** to stop.

**4. Look at your calendar**

```bash
.venv/bin/seth-agent calendar
```

```
2026-10-18  #3 Dad's flight
               Arriving in the afternoon; Alex will pick him up.
2026-10-19  #1 School closed
Nov 2026    #2 Workout session (rescheduled)
```

## Everyday use

| Command | What it does |
|---|---|
| `seth-agent run` | Keeps watching for new messages and serves the calendar at `http://127.0.0.1:8765` |
| `seth-agent once` | Catches up on any new messages, prints a summary and exits. Handy for a quick refresh or a scheduled job |
| `seth-agent calendar` | Prints upcoming events, from today onward |
| `seth-agent calendar --from 2026-11-01 --to 2026-11-30` | Prints events in a date range |
| `seth-agent calendar --all` | Also shows cancelled events |

Add `-v` before any command for detailed logs, e.g. `seth-agent -v once`.

All commands live in `.venv/bin/`. To type just `seth-agent`, activate the environment first with `source .venv/bin/activate`.

## Reading the calendar

While `seth-agent run` is running, any app on your Mac can read the calendar over HTTP:

```bash
curl -s 'http://127.0.0.1:8765/calendar?from=2026-10-01&to=2026-12-31'
```

```json
{
  "from": "2026-10-01",
  "to": "2026-12-31",
  "days": {
    "2026-10-18": [{ "id": 3, "title": "Dad's flight", "precision": "day", "date_start": "2026-10-18", "date_end": "2026-10-18", ... }],
    "2026-10-19": [{ "id": 1, "title": "School closed", ... }],
    "Nov 2026":   [{ "id": 2, "title": "Workout session (rescheduled)", "precision": "month", "date_start": "2026-11-01", "date_end": "2026-11-30", ... }]
  },
  "events": [ ... ]
}
```

To see why an event exists, ask for it by id. The response includes the messages behind it and its full change history:

```bash
curl -s http://127.0.0.1:8765/events/3
```

To check the agent is healthy:

```bash
curl -s http://127.0.0.1:8765/health
```

You can also open **http://127.0.0.1:8765/docs** in a browser to try every endpoint interactively. The complete reference is in [docs/API.md](docs/API.md).

## Configuration

The defaults match a standard setup, so most people won't need to change anything. To change a setting, set an environment variable before running:

| Variable | Default | What it controls |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Your Claude API key. **Required** |
| `SETH_SOURCE_DB` | `/Users/tnpbot/github.com/seth-ingestion/data/messages.db` | Where seth-ingestion stores messages |
| `SETH_MEMORY_DB` | `./data/memory.db` | Where the calendar is kept |
| `SETH_POLL_SECONDS` | `15` | How often to check for new messages |
| `SETH_TZ` | your Mac's timezone | Timezone for working out dates, e.g. `America/Los_Angeles` |
| `SETH_MODEL` | `claude-opus-5-5` | The Claude model that reads the messages |
| `SETH_EFFORT` | `medium` | How carefully Claude thinks: `low` is cheaper and faster, `high` is more careful with tricky dates |
| `SETH_BATCH_SIZE` | `20` | Messages per Claude request |
| `SETH_API_HOST` / `SETH_API_PORT` | `127.0.0.1` / `8765` | Where the HTTP API listens |

Example:

```bash
SETH_POLL_SECONDS=60 SETH_EFFORT=low .venv/bin/seth-agent run
```

## Troubleshooting

**"no Anthropic credentials: set ANTHROPIC_API_KEY"**
The agent can't reach Claude without a key. Set `ANTHROPIC_API_KEY` and start it again. Nothing is lost: the waiting messages are processed as soon as the key works.

**The calendar is empty**
- Run `seth-agent -v once` and read the summary. `0 candidates` means seth-ingestion hasn't captured any messages yet.
- Remember that most messages aren't events. Greetings, questions and photos without captions are skipped on purpose.
- `seth-agent calendar` only shows events from today onward. Use `--from` to look further back.

**An event has the wrong date or shouldn't be there**
Open `http://127.0.0.1:8765/events/<id>` to see which messages it came from. If Claude often misreads your messages, try `SETH_EFFORT=high`. If the date depends on context the agent doesn't have, it can only go by what the messages say.

**"messages.db not openable read-only … falling back to immutable mode"**
This is harmless. It happens when seth-ingestion isn't running, and the agent reads the file another way. Messages that arrive later are picked up once seth-ingestion is running again.

**Port 8765 is already in use**
Another copy of the agent may already be running. Stop it, or choose a different port with `SETH_API_PORT=8766`.

**Start over from scratch**
Stop the agent and delete `data/memory.db`. The next run rebuilds the calendar from every captured message. This makes one Claude request per 20 or so messages.

## Privacy

- **Everything stays on your Mac**, except the message text sent to Claude to read.
- **Media files aren't sent.** Only text and captions are.
- **The API listens only on your own machine** (`127.0.0.1`), so other devices on your network can't reach it.
- **The agent never changes seth-ingestion's database.** It only reads it.
- The calendar is a single SQLite file in `data/`, which git ignores.

## More documentation

| Document | For |
|---|---|
| [docs/API.md](docs/API.md) | Building an app on top of the calendar: every endpoint, field and status code |
| [docs/MEMORY.md](docs/MEMORY.md) | How the memory database is laid out, with ready-to-run SQL queries |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Working on the agent: architecture, how extraction works, testing, and adding new kinds of memory |
