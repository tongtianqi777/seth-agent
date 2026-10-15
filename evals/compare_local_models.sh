#!/usr/bin/env bash
# Run the three live evals against one or more Ollama models and keep the output, to compare them.
#
#   evals/compare_local_models.sh qwen3:30b-a3b qwen3:32b
#   SCENARIOS="chinese_school chinese_volunteering" evals/compare_local_models.sh qwen3:30b-a3b
#
# For each model it builds a copy with a long context window (see docs/OLLAMA_SETUP.md, step 4), runs
# calendar_examples.py, volunteering_examples.py and bilingual_examples.py, and writes the output to
# data/eval-results/<model>.txt (git ignores data/). The copy is removed afterwards. Weights are shared, so it
# costs no extra disk space.
#
# Settings (environment):
#   NUM_CTX      context window of the copy          default 40960 (room for the prompt plus MAX_TOKENS)
#   MAX_TOKENS   SETH_LOCAL_MAX_TOKENS for the run   default 32000 (a reasoning model can spend thousands per batch)
#   BATCH_SIZE   SETH_BATCH_SIZE for the run         default 8
#   SCENARIOS    bilingual scenarios to run          default all (see the top of bilingual_examples.py)
#
# Rerun a failed pair for comparison, e.g. the ones that missed on qwen3.5:9b:
#   SCENARIOS="chinese_school chinese_volunteering" evals/compare_local_models.sh qwen3:32b
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <ollama model> [<ollama model> ...]" >&2
  exit 2
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/data/eval-results"
mkdir -p "$out"
python="$root/.venv/bin/python"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

for model in "$@"; do
  name="seth-eval-$(echo "$model" | tr ':/' '--')"
  file="$out/$(echo "$model" | tr ':/' '--').txt"
  printf 'FROM %s\nPARAMETER num_ctx %s\nPARAMETER temperature 0\n' "$model" "${NUM_CTX:-40960}" > "$work/Modelfile"
  ollama create "$name" -f "$work/Modelfile" > /dev/null
  echo "== $model (as $name), results in $file"
  {
    echo "model: $model  num_ctx: ${NUM_CTX:-40960}  max_tokens: ${MAX_TOKENS:-32000}  batch: ${BATCH_SIZE:-8}  date: $(date -u +%FT%TZ)"
    export SETH_PROVIDER=local SETH_LOCAL_MODEL="$name" SETH_LOCAL_TIMEOUT=3600 \
           SETH_LOCAL_MAX_TOKENS="${MAX_TOKENS:-32000}" SETH_BATCH_SIZE="${BATCH_SIZE:-8}"
    for script in calendar_examples volunteering_examples; do
      echo; echo "### $script"
      "$python" "$root/evals/$script.py" || true
    done
    echo; echo "### bilingual_examples ${SCENARIOS:-}"
    # shellcheck disable=SC2086
    "$python" "$root/evals/bilingual_examples.py" ${SCENARIOS:-} || true
  } 2>&1 | tee "$file"
  ollama rm "$name" > /dev/null
done

echo
echo "Summary (PASS/FAIL counts per model):"
for f in "$out"/*.txt; do
  printf '%s: %s PASS, %s FAIL\n' "$(basename "$f" .txt)" "$(grep -c '^PASS' "$f" || true)" "$(grep -c '^FAIL' "$f" || true)"
done
