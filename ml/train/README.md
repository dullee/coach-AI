# Fine-tuning spike: step by step

Goal: find out whether a small model on your own GPU can write coaching reports as well as a
big cloud model. Teacher: DeepSeek V4.1 Flash via Pollinations. Student: Qwen3-8B with QLoRA on
Kaggle. Serving: Ollama on your RTX 4060.

## 1. Training data (your PC)

```
$env:POLLINATIONS_API_KEY = "sk_..."
.venv\Scripts\python -m ml.dataset.inputs --own-account <your account id>   # ~450 summaries (done once)
.venv\Scripts\python -m ml.dataset.generate --limit 5                       # quick test
.venv\Scripts\python -m ml.dataset.generate                                 # all of them; resumable
.venv\Scripts\python -m ml.dataset.export                                   # -> data\ml\dataset\v1\
```

`generate` can be stopped (Ctrl+C) and run again: finished inputs are skipped.
`export` keeps only teacher reports that pass validation and the audit, and splits them by match
(80% train, 10% validation, 10% test).

## 2. Train on Kaggle

1. kaggle.com -> **Datasets -> New Dataset** -> upload everything in `data\ml\dataset\v1\`
   (train.jsonl, val.jsonl, train_qlora.py, Modelfile, ...). Name it e.g. `deadlock-coach-v1`.
2. **Code -> New Notebook**. In the right panel: **Accelerator: GPU T4 x2** (or P100),
   **Internet: on**, and **Add Input** -> your dataset.
3. Paste this into the notebook's code cell (installs the versions from Unsloth's Qwen3
   notebook, 2026-10, finds your dataset folder by itself, and trains):
   ```
   !pip install -q unsloth
   !pip install -q transformers==4.57.6
   !pip install -q --no-deps trl==0.22.2
   import glob, os
   script = glob.glob("/kaggle/input/**/train_qlora.py", recursive=True)[0]
   data = os.path.dirname(script)
   print("dataset folder:", data)
   !python "{script}" --data "{data}"
   ```
4. To run it without keeping the browser open: **Save Version -> Save & Run All (Commit) -> Save**.
   The run continues on Kaggle's servers; come back later and open the version's **Output** tab.
   Training ~350 examples x 2 epochs takes roughly 30-60 min on a T4; building the GGUF file
   another ~10-20 min.
5. When it finishes, the **Output** panel has `coach-q4_k_m.gguf` (~5 GB), `coach_lora/` and
   `training_log.json`. Download the `.gguf` and `training_log.json`.

If the notebook runs out of disk while building the GGUF (or the Output has only `coach_lora/`),
you don't need to rerun. Ollama 0.40+ no longer loads LoRA adapters, so serve the adapter with
llama.cpp's `llama-server` (included in the Ollama download, or from a llama.cpp release) on top of
the `qwen3:8b` file Ollama already downloaded:
```
# 1. adapter -> GGUF (~90 MB), with llama.cpp's converter (needs torch, transformers, gguf)
python llama.cpp/convert_lora_to_gguf.py coach_lora --base-model-id Qwen/Qwen3-8B --outtype f16 --outfile coach-lora-f16.gguf
# 2. find the base model file:  ollama show qwen3:8b --modelfile   (the FROM line)
# 3. serve (thinking off, as in training)
llama-server -m <FROM path> --lora coach-lora-f16.gguf --chat-template-file coach_lora/chat_template.jinja --chat-template-kwargs '{"enable_thinking":false}' -c 6144 --temp 0.3 --port 8081 --alias coach
```
Then point the app and the evaluation at it with `COACH_LOCAL_URL=http://localhost:8081/v1` and use
`local:coach` as usual. Tested on 2026-10-21: the adapter loads. An 8 GB Mac is too small to run an
8B model at usable speed, so use the RTX 4060 PC.

## 3. Run it locally with Ollama

1. Install Ollama from https://ollama.com.
2. Put `coach-q4_k_m.gguf` in a folder together with `Modelfile` (from the dataset folder) and run there:
   ```
   ollama create coach -f Modelfile
   ```
3. For the comparison, the untuned base model served the same way:
   ```
   ollama pull qwen3:8b
   ollama create coach-base -f Modelfile.base
   ```
4. Use it in the app: `$env:COACH_LOCAL_MODEL = "coach"`, restart the app, pick **Local model**.

## 4. Compare

```
.venv\Scripts\python -m ml.eval.run_eval --models local:coach local:coach-base pollinations:deepseek/deepseek-v4.1-flash
```

Writes `data\ml\eval\<time>.md`: validity, invented numbers, audit findings, speed and cost per
model on the held-out test summaries, plus 3 example reports side by side.
