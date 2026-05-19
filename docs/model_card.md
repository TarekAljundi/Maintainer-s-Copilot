# Model Card — Classifier v1

## Architecture
- Base: `microsoft/deberta-v3-small` (44M params, 12 layers, 384 hidden)
- Head: linear over 4 classes (bug, feature, docs, question)
- Tokenizer: DebertaV2Tokenizer (sentencepiece), max length 512

## Training data
- Source: `tiangolo/fastapi` closed issues, ≥ 2020-01-01
- Label mapping: strict + tiebreak `bug > feature > docs > question`
- Splits: 70/10/15 train/val/test, time-stratified by `closed_at` ascending
- Preprocessing: title+body, code blocks -> `<CODE>` placeholder, 512-tok head trunc
- Data hash (sha256 of `data/labeled.jsonl`): TBD

## Hyperparameters
- Freeze policy: full fine-tune
- LR: encoder 2e-5, classifier head 1e-4 (discriminative)
- Scheduler: linear warmup 10%, linear decay
- Weight decay: 0.01 (non-bias, non-LN)
- Batch size: 16
- Epochs: 3-5, early stop on val macro-F1, patience 1
- Temperature: n/a (classification head, not generative)

## Metrics (test split)
- Macro-F1: TBD
- Per-class F1: bug=TBD, feature=TBD, docs=TBD, question=TBD
- Confusion matrix: see `reports/classification.json`
- p50 latency: TBD ms
- p99 latency: TBD ms

## Weights
- File: `models/classifier/v1/model.safetensors` (MinIO)
- SHA-256: TBD (pinned here; boot check compares against runtime)

## Limitations
- Trained on FastAPI-only English issues. No generalization claim.
- Head-only truncation biases toward signal in first 512 tokens.
- `<CODE>` placeholder strips lexical content of code blocks.
