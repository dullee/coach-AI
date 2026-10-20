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

If the notebook runs out of disk while building the GGUF, rerun with `--no-gguf`: you then get
just the LoRA adapter, and we convert it on your PC instead.

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
