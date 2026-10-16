# Choosing a local model

Which model should read the school's messages when it isn't Claude, and why. For installing and running one, see [OLLAMA_SETUP.md](OLLAMA_SETUP.md).

**How much to trust this document.** The recommendations come from what the task needs, what the hardware allows, and general guides to local models. I found no benchmark that tests this exact job (structured calendar extraction from English and Chinese chat messages), and none comparing the candidates on it. Treat the shortlist as a starting point and the [evals](#how-to-decide-between-models) as the deciding vote. Model names and tags change quickly, so check the Ollama library for the current ones.

## What the job demands

The agent reads batches of WhatsApp messages and fills in a typed list of calendar operations (see [DEVELOPMENT.md](DEVELOPMENT.md#the-output)). Four properties of that job decide what makes a model good for it:

1. **Chinese and English, mixed.** Messages, names and sentences switch between both, and the prompts include Chinese date words ("下下周", "月底", "改到10月20号"). A model that is strong in English but weak in Chinese will miss events.
2. **Date arithmetic and judgment.** "Next Friday", "the week of the 26th", "it's Sarah this week" (so which day, and who?). The hardest call is *add vs. update vs. duplicate*: deciding that "the flight's moved to Tuesday" refers to an event already on the calendar. This is where small models fail first.
3. **Reliable structured output.** Every answer must parse into the schema. A malformed answer means a failed batch.
4. **A long prompt and a short answer.** The prompt holds instructions, the calendar, earlier messages and new ones (roughly 8–16k tokens). The answer is usually an empty list or a few events.

Two things the job does *not* demand:

- **Speed.** Message volume is low and nobody waits on a batch. The only time speed matters is rebuilding the calendar from scratch, which is a one-off. This favours the more accurate model over the faster one.
- **Creativity or long writing.** The newsletters and yearbook on the roadmap will want that, but they are separate jobs and can use a different model.

There is also a requirement the task adds: **messages are untrusted input.** Anyone in a group can write anything, and the prompt tells the model to treat it as data. Larger models follow that rule more reliably, which is another reason not to go small.

## What the hardware allows

An Apple M5 Max with 64 GB of unified memory. Unified memory is shared by the system, the model's weights and the model's cache of what it has read, so the budget is roughly:

| Part | Memory |
|---|---|
| macOS and other apps | ~8–12 GB |
| Model weights (30–32B parameters at 4-bit) | ~18–20 GB |
| Context cache (32k tokens) | ~2–6 GB |
| Left over | ~25 GB or more |

So a model of about 30B parameters fits easily, with room for a second model (for example one that reads photos) to stay loaded beside it. A 70B-class model *can* be squeezed in at 4-bit (about 40 GB), but it leaves little room, runs slowly, and the gain over a good 30B model is smaller than the cost. I'd skip it.

4-bit quantization (the usual "Q4" builds) shrinks the weights to about a quarter of their full size for a small loss in quality. For extraction, which needs reading comprehension more than fine-grained recall, that trade is worth it. If memory is spare and accuracy is the problem, a higher-precision build (8-bit, about twice the size) of the same model is the first thing to try, before a different model.

## The shortlist

| Candidate | Type | Size at 4-bit | Why it's on the list | Watch out for |
|---|---|---|---|---|
| **Qwen 30B-class, dense or mixture-of-experts** (the `qwen3` family; a newer Qwen release may supersede it) | Text, or text + vision in newer releases | ~18–20 GB | Strongest Chinese of the open models. Follows JSON schemas reliably. Has vision-language siblings, so one family could cover the calendar and photos | The dense model is slower; the mixture-of-experts one is a little less accurate on tricky dates |
| **Gemma 4, the larger sizes** | Text + vision | ~18–20 GB | Natively multimodal. The mixture-of-experts size is fast | No evidence I found on its Chinese quality, which is the thing this school most needs |
| Smaller models (7–14B) | Text | 5–9 GB | Fast, and leave the most memory free | Fail at the add/update/duplicate judgment and at injection resistance; fine for a pre-filter, not for the calendar |
| 70B-class dense models | Text | ~40 GB | Highest quality of what *can* load | Leaves no room for a second model, slow, and the marginal gain is small |

Two terms: a **dense** model uses all its parameters for every token, so it is slower but usually more accurate for its size. A **mixture-of-experts** (MoE) model has the same total size but uses only a fraction per token (a "30B-A3B" activates about 3B), so it is several times faster at a small cost in accuracy.

### The recommendation

1. **Evaluate two Qwen builds first:** the 30B-class MoE (the current default, `qwen3:30b-a3b`) and the 32B-class dense model. Because volume is low and accuracy is what is hard, I expect the dense model to be the better *final* choice and the MoE the better *fallback*, if the dense model proves too slow for the rebuild case. This is a prediction, not a result.
2. **Try Gemma 4 as the challenger,** mainly to see whether its Chinese holds up. Its vision strength makes it the main alternative if the photo work matters more than the calendar.
3. **Make one model do both text and photos if the evals allow it.** One loaded model is simpler to run and leaves memory free. If the best text model is weak at vision (or the reverse), run two with `OLLAMA_MAX_LOADED_MODELS=2`; the memory budget above allows it.
4. **Keep Claude for the hard cases.** A local model is the right default when privacy is the point; Claude is the better reader. They aren't exclusive (see [When to stay on Claude](#when-to-stay-on-claude)).

The default in the code is now `gemma4:31b`, which passed all 31 checks in `compare_local_models.sh` (2026-10-16) against 29 for `qwen3:32b`, 26 for `gemma4:26b-a4b` and 25 for `qwen3:30b-a3b`. Set `SETH_LOCAL_MODEL` to use another.

## How to decide between models

Run the three live checks against each candidate, with the same context size, and compare against Claude on the same checks:

```bash
SETH_PROVIDER=local SETH_LOCAL_MODEL=<model> .venv/bin/python evals/calendar_examples.py
SETH_PROVIDER=local SETH_LOCAL_MODEL=<model> .venv/bin/python evals/volunteering_examples.py
SETH_PROVIDER=local SETH_LOCAL_MODEL=<model> .venv/bin/python evals/bilingual_examples.py
```

The first two are short English checks. As a sanity check, a 9B model (`qwen3.5:9b`, run on a 32 GB dev Mac with a 16k context) passed every check in both, so they cannot tell a good candidate from a merely adequate one. They are there to confirm the plumbing works. The third, [bilingual_examples.py](../evals/bilingual_examples.py), is the one to decide with. Its scenarios, each starting from an empty calendar:

- **Chinese school dates** (下周一学校放假, 下下周五, 11月5号), a flight corrected in Chinese, and greetings that must produce nothing.
- **Mixed Chinese and English** in one message.
- **Chinese volunteering** ("这周是我", "下周我老婆来"), then a drop-out that must update the event rather than add one.
- **The same news in two groups and two languages**, which must stay one event.
- **Prompt injection**, in English and Chinese: the calendar must not change.

Still worth adding as you go:

- **Real messages that went wrong** in the past, with the correct calendar for each. These are worth more than invented ones.
- **Run each candidate three times.** Temperature is 0, but a local server's output can still vary slightly between runs. A model that passes sometimes isn't passing.
- **Watch the time.** Models that reason before answering (the Qwen3 family does) can spend thousands of tokens per batch. The 9B above generated 4,000+ tokens per batch at about 22 tokens/s on a 32 GB M1 Pro, around three minutes each. The M5 Max will be much faster, but compare seconds per batch, not just pass rates.

[compare_local_models.sh](../evals/compare_local_models.sh) does the setup and the three runs for you: `evals/compare_local_models.sh qwen3:30b-a3b qwen3:32b`. For each model it builds a copy with a long context window, runs the evals with the output limit raised, saves the output to `data/eval-results/<model>.txt` and prints pass counts. Set `SCENARIOS` to run only some of the bilingual scenarios.

Record the results where you can compare them:

| Model | Build / context | English | Chinese | Add vs. update | Injection | Seconds per batch | Memory (`ollama ps`) |
|---|---|---|---|---|---|---|---|
| Claude (reference) | | | | | | | n/a |
| Qwen 30B-class MoE | | | | | | | |
| Qwen 32B-class dense | | | | | | | |
| Gemma 4 | | | | | | | |

Decide by these rules, in order:

1. **No failures on the add/update/duplicate cases and the injection case.** A calendar with duplicate or wrongly cancelled events is worse than one with a missing event, because nobody notices the first kind.
2. **Chinese within one miss of English.** A model that reads English well and Chinese poorly is a bad fit for this school, whatever its overall score.
3. **Then the fastest model that passes.** Speed is the tie-breaker, not the goal.

## When to stay on Claude

- **The hard groups.** Volunteering groups depend on inferring who is on duty from "it's me this week" and the group's routine. If a local model fails these checks, run it on the school calendar and keep volunteering on Claude. (Today the provider is chosen for the whole agent, so this would take a small change to choose per extractor.)
- **Mistakes that matter.** Cancelling a real event on a bad reading costs more than the API call saved.
- **Rebuilds and backfills.** A one-time full rebuild is cheap on the API and slow locally.

The reasons to go local are privacy (nothing leaves the machine), no per-message cost, and working offline. If none of those matter to you for a given job, Claude is simply the better reader.

## Photos

The roadmap's media library and yearbook need a model that can look at images. The same shortlist applies, because the leading open models (Qwen's vision-language models, Gemma 4) take images and text. Three things are specific to photos:

- **Memory:** an image costs extra memory for the vision encoder and for the image's tokens. Plan for a few GB beyond the weights.
- **Text inside photos** (flyers, posters, schedules) is often Chinese. Test it on real examples before choosing; a dedicated OCR model is the fallback if a general model reads it poorly.
- **Searching by meaning** ("photos from the Christmas program") is better served by a small embedding model than by asking the big model about every photo. It uses under 1 GB and stores one vector per photo.

Photos of children are a good reason to keep image work local. If a hard case ever goes to Claude, send the caption or extracted text, not the photo. Describing a scene is low-risk; identifying or grouping children by face is a different matter and shouldn't be built without a deliberate decision.

## Revisit this

Local models improve every few months. Rerun the evals when a new release of the Qwen or Gemma families lands, or before a major change to the prompts. Update the results table, and the default in [config.py](../src/seth_agent/config.py) if the winner changes.
