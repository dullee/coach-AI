"""QLoRA fine-tune of Qwen3-8B on the coaching dataset (runs on Kaggle; see ml/train/README.md).

    python train_qlora.py --data /kaggle/input/<your-dataset> [--epochs 2] [--max-seq 4096]

Self-contained (no project imports): the dataset folder from `python -m ml.dataset.export`
holds train.jsonl / val.jsonl and a copy of this script.

API usage follows Unsloth's official Qwen3 notebooks (checked 2026-10-21):
FastLanguageModel.from_pretrained / get_peft_model, trl SFTTrainer + SFTConfig,
unsloth.chat_templates.train_on_responses_only, save_pretrained_gguf(q4_k_m).

Thinking is turned off: every example is rendered with the Qwen3 chat template and
enable_thinking=False, which puts an empty <think></think> block before the answer. The
Ollama Modelfile adds the same block, so training and serving see the same text.
Loss is computed on the answer (the report JSON) only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

BASE_MODEL = "unsloth/Qwen3-8B-unsloth-bnb-4bit"   # Apache 2.0; Unsloth's 4-bit build
RESPONSE_PART = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
INSTRUCTION_PART = "<|im_start|>user\n"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path, help="folder with train.jsonl and val.jsonl")
    ap.add_argument("--out", type=Path, default=Path("/kaggle/working"))
    ap.add_argument("--work", type=Path, default=Path("/tmp/coach_build"), help="scratch space for the 16 GB merge")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--max-seq", type=int, default=4096)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--no-gguf", action="store_true", help="only save the LoRA adapter")
    args = ap.parse_args()

    from unsloth import FastLanguageModel  # import first: Unsloth patches transformers/trl
    from unsloth.chat_templates import train_on_responses_only
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL, max_seq_length=args.max_seq, load_in_4bit=True, load_in_8bit=False, full_finetuning=False)
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=args.rank, lora_dropout=0, bias="none",
        use_gradient_checkpointing="unsloth", random_state=3407)

    def render(example: dict) -> str:
        msgs = example["messages"]
        prompt = tokenizer.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        assert prompt.endswith(RESPONSE_PART), "chat template changed: update RESPONSE_PART"
        return prompt + msgs[-1]["content"] + "<|im_end|>\n"

    def as_dataset(rows: list[dict]) -> Dataset:
        texts = [render(r) for r in rows]
        lengths = [len(tokenizer(t).input_ids) for t in texts]
        keep = [t for t, n in zip(texts, lengths) if n <= args.max_seq]
        print(f"  {len(keep)}/{len(texts)} examples fit in {args.max_seq} tokens (longest {max(lengths)})")
        return Dataset.from_dict({"text": keep})

    print("train:")
    train = as_dataset(load_jsonl(args.data / "train.jsonl"))
    print("val:")
    val = as_dataset(load_jsonl(args.data / "val.jsonl"))

    trainer = SFTTrainer(
        model=model, tokenizer=tokenizer, train_dataset=train, eval_dataset=val,
        args=SFTConfig(
            dataset_text_field="text",   # sequence length comes from from_pretrained(max_seq_length=...)
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            warmup_steps=5,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            logging_steps=5,
            eval_strategy="epoch",
            optim="adamw_8bit",
            weight_decay=0.001,
            lr_scheduler_type="linear",
            seed=3407,
            report_to="none",
            output_dir=str(args.work / "checkpoints"),
        ),
    )
    trainer = train_on_responses_only(trainer, instruction_part=INSTRUCTION_PART, response_part=RESPONSE_PART)

    start = time.time()
    stats = trainer.train()
    minutes = (time.time() - start) / 60
    log = {"base_model": BASE_MODEL, "train_examples": len(train), "val_examples": len(val), "epochs": args.epochs,
           "rank": args.rank, "lr": args.lr, "max_seq": args.max_seq, "minutes": round(minutes, 1),
           "train_loss": stats.training_loss, "history": trainer.state.log_history}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "training_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"trained in {minutes:.0f} min, final train loss {stats.training_loss:.3f}")

    adapter = args.out / "coach_lora"
    model.save_pretrained(str(adapter))
    tokenizer.save_pretrained(str(adapter))
    print(f"LoRA adapter saved to {adapter}")

    if not args.no_gguf:
        gguf_dir = args.work / "coach_gguf"   # the merge needs ~16 GB: build outside /kaggle/working (20 GB cap)
        model.save_pretrained_gguf(str(gguf_dir), tokenizer, quantization_method="q4_k_m")
        for f in Path(gguf_dir).rglob("*.gguf"):
            if "q4_k_m" in f.name.lower() or f.stat().st_size < 7e9:
                target = args.out / "coach-q4_k_m.gguf"
                shutil.copy2(f, target)
                print(f"GGUF for Ollama: {target} ({f.stat().st_size / 1e9:.1f} GB)")
                break
        else:
            print(f"no q4_k_m GGUF found in {gguf_dir}: {os.listdir(gguf_dir)}")


if __name__ == "__main__":
    main()
