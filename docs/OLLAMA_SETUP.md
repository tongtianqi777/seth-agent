# Setting up Ollama on a Mac

This guide gets a local model running on a Mac and connects seth-agent to it, so the school's messages are read on the machine and never leave it. For which model to pull and why, see [LOCAL_MODEL_CHOICE.md](LOCAL_MODEL_CHOICE.md).

It is written for an Apple Silicon MacBook Pro with 64 GB of unified memory. The steps are the same on smaller Macs; only the model choice changes (see [Smaller Macs](#smaller-macs)).

## 1. Install Ollama

```bash
brew install ollama
```

Or download the app from [ollama.com](https://ollama.com). The app starts the server for you and puts an icon in the menu bar. The Homebrew install gives you only the command line, which suits a machine that runs the agent all day.

Check it:

```bash
ollama --version
```

## 2. Start the server

With Homebrew, run it as a background service that restarts after a reboot:

```bash
brew services start ollama
```

The server listens on `http://127.0.0.1:11434`. Check that it answers:

```bash
curl http://127.0.0.1:11434/api/version
```

Leave it on `127.0.0.1`. Don't set `OLLAMA_HOST=0.0.0.0`: the server has no authentication, and it would let anyone on the network use your Mac to run models.

## 3. Pull a model

```bash
ollama pull qwen3:30b-a3b
```

The download is about 18–20 GB. Models are kept in `~/.ollama/models`, which is also where the disk space goes if you try several, so remove the ones you drop (`ollama rm <name>`).

The tag above is the one seth-agent defaults to. Tags change as new releases arrive, so before pulling check the library page for the family ([qwen3](https://ollama.com/library/qwen3), [gemma4](https://ollama.com/library/gemma4), [qwen3-vl](https://ollama.com/library/qwen3-vl)) and pick the 4-bit build of the size you want. See what you have with `ollama list`.

Try it by hand first:

```bash
ollama run qwen3:30b-a3b "下周一学校放假。Which date is that if today is Monday 2026-10-12?"
```

## 4. Give it a long enough context

This step matters more than any other, and it is easy to miss.

Ollama gives a model a fixed context window (how much text it can read at once), and **silently cuts off whatever doesn't fit**. seth-agent's prompt holds the instructions, the current calendar, 15 earlier messages and up to 20 new ones. The volunteering prompt alone is roughly 3,000 tokens. With a small default window the model would never see the new messages at the end, and the calendar would quietly come out wrong.

The OpenAI-compatible endpoint seth-agent uses cannot set the window per request, so set it on the model. Create a file named `Modelfile` anywhere (it doesn't need to live in this repo):

```
FROM qwen3:30b-a3b
PARAMETER num_ctx 32768
PARAMETER temperature 0
```

Then build a named copy of the model. It shares the downloaded weights, so it takes no extra disk space:

```bash
ollama create seth-reader -f Modelfile
```

Use `seth-reader` as the model name from here on. 16384 is the least I'd use; 32768 leaves room for a long group history and for photo work later. A bigger window costs memory, because the model keeps a cache of everything it has read.

Verify it took effect by running the model and then looking at the loaded size:

```bash
ollama run seth-reader "hi" && ollama ps
```

`ollama ps` shows the model's memory use and context. The `PROCESSOR` column should say `100% GPU`. If it shows a CPU split, the model plus its cache doesn't fit in memory, and it will be very slow: lower `num_ctx` or choose a smaller model.

## 5. Keep the model loaded

By default Ollama unloads a model after 5 minutes idle, and loading a 20 GB model takes a while. seth-agent polls every 15 seconds but often has nothing to do, so the first batch after a quiet spell would wait for the reload. Keep the model in memory:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE 24h
brew services restart ollama
```

`launchctl setenv` lasts until you log out; to make it permanent, put the variable in the service's plist or in your shell profile if you start Ollama from a terminal (`export OLLAMA_KEEP_ALIVE=24h; ollama serve`). Other settings worth knowing:

| Variable | What it does |
|---|---|
| `OLLAMA_KEEP_ALIVE` | How long a model stays loaded after its last request. `-1` means forever |
| `OLLAMA_MAX_LOADED_MODELS` | How many models may be loaded at once. Raise it to `2` if you run a text model and a vision model side by side |
| `OLLAMA_NUM_PARALLEL` | How many requests are served at once. seth-agent sends one at a time, so leave it at `1`; each extra slot multiplies the memory used by the context |
| `OLLAMA_FLASH_ATTENTION=1` | Cuts the memory used by long contexts. Recommended |
| `OLLAMA_KV_CACHE_TYPE=q8_0` | Halves the cache memory again, with a small quality cost. Needs flash attention. Try it only if memory is tight |

## 6. Point seth-agent at it

```bash
SETH_PROVIDER=local SETH_LOCAL_MODEL=seth-reader SETH_BATCH_SIZE=8 .venv/bin/seth-agent run
```

`SETH_LOCAL_URL` already defaults to Ollama's address (`http://127.0.0.1:11434/v1`). `SETH_BATCH_SIZE=8` is a starting point: local models are less accurate on long batches, so smaller batches are safer. The full list of settings is in the [README](../README.md#configuration).

To make the choice stick, put the variables in your `~/.zshrc`, or in whatever starts the agent.

## 7. Check that it works

First, a quick structured-output test, which is what seth-agent depends on:

```bash
curl -s http://127.0.0.1:11434/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "seth-reader",
  "messages": [{"role": "user", "content": "List two fruits."}],
  "response_format": {"type": "json_schema", "json_schema": {"name": "Fruits", "strict": true,
    "schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}},
               "required": ["items"]}}}
}' | python3 -m json.tool
```

The reply's `content` should be a JSON object like `{"items": ["apple", "banana"]}`.

Then the agent's own checks, which use the same model settings:

```bash
SETH_PROVIDER=local SETH_LOCAL_MODEL=seth-reader .venv/bin/python evals/calendar_examples.py
SETH_PROVIDER=local SETH_LOCAL_MODEL=seth-reader .venv/bin/python evals/volunteering_examples.py
```

Every line should say `PASS`. A `FAIL` means this model misread something that Claude gets right, which is useful to know before it touches the real calendar. [LOCAL_MODEL_CHOICE.md](LOCAL_MODEL_CHOICE.md#how-to-decide-between-models) explains how to compare models with these.

Last, process a few real messages without leaving it running, and read the result:

```bash
SETH_PROVIDER=local SETH_LOCAL_MODEL=seth-reader .venv/bin/seth-agent -v once
.venv/bin/seth-agent calendar
```

Use a separate `SETH_MEMORY_DB` for experiments (for example `SETH_MEMORY_DB=/tmp/local-test.db`), so a weak model doesn't write into the real calendar. Switching the provider later does not redo old messages, since they are already marked processed.

## Using MLX instead of Ollama

[`mlx-lm`](https://github.com/ml-explore/mlx-lm) runs models with Apple's own framework, and is sometimes faster on Apple Silicon. It also serves an OpenAI-compatible API:

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/<model-name> --port 8080
SETH_PROVIDER=local SETH_LOCAL_URL=http://127.0.0.1:8080/v1 SETH_LOCAL_MODEL=mlx-community/<model-name> .venv/bin/seth-agent run
```

Whether it enforces the JSON schema depends on the version. seth-agent also puts the schema in the prompt and validates the answer, so it works either way, but test it with the evals before relying on it. Ollama is simpler to operate (model downloads, keep-alive and the service are handled), which is why this guide uses it.

## Smaller Macs

The model plus its context must fit in unified memory with room left for macOS, roughly: model size + 2–6 GB of cache + 8 GB for the system.

| Memory | What fits comfortably |
|---|---|
| 64 GB | A 30–32B model at 4-bit with a 32k context, plus a small model next to it |
| 32 GB | A 30B-class MoE at 4-bit with a 16k context is tight; a 14B model is comfortable |
| 16 GB | A 7–9B model. Expect weaker date reasoning, so stay on Claude for the calendar if you can |

macOS lets the GPU use only part of unified memory by default (roughly two thirds to three quarters). If `ollama ps` shows a CPU split on a model that should fit, the limit can be raised until the next reboot with `sudo sysctl iogpu.wired_limit_mb=<megabytes>`. Leave at least 8 GB for the system.

## Troubleshooting

**"local model server unreachable"** (in the agent's log). The server isn't running. Start it with `brew services start ollama` and check `curl http://127.0.0.1:11434/api/version`. Nothing is lost: the batch is retried automatically.

**"404 … model not found".** The name in `SETH_LOCAL_MODEL` isn't one `ollama list` shows. If you made `seth-reader`, it is listed as `seth-reader:latest`, and `seth-reader` alone works.

**Events that ignore the newest messages.** The context window is too small and the end of the prompt is being cut off. Redo [step 4](#4-give-it-a-long-enough-context). Look for a "truncating input prompt" warning in Ollama's log (`~/.ollama/logs/server.log`).

**The first request after a quiet period is slow.** The model was unloaded. Set `OLLAMA_KEEP_ALIVE` ([step 5](#5-keep-the-model-loaded)).

**Everything is very slow.** Run `ollama ps`. If `PROCESSOR` isn't `100% GPU`, the model doesn't fit: lower `num_ctx`, turn on flash attention, or pick a smaller model. Also check that nothing else large is using the memory.

**The agent logs "the local model's output didn't match the schema".** The model produced malformed or off-schema JSON, and that batch was marked failed (see `extraction_errors` in the [memory docs](MEMORY.md)). It happens more with smaller models and larger batches. Lower `SETH_BATCH_SIZE`, and if it persists, use a bigger model.
