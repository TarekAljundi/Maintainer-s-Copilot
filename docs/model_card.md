# Model Card — Classifier v1

## Architecture
- Base: `microsoft/deberta-v3-small` (44M params, 12 layers, 384 hidden)
- Head: linear over 4 classes (bug, feature, docs, question)
- Tokenizer: DebertaV2Tokenizer (sentencepiece), max length 512

## Training data
- Source: `fastapi/fastapi` closed issues, >= 2020-01-01
- Label mapping: strict + tiebreak `bug > feature > docs > question`
- Splits: 70/10/15 train/val/test, time-stratified by `closed_at` ascending
- Preprocessing: title+body, code blocks -> `<CODE>` placeholder, 512-tok head trunc
- Dataset manifest sha256: `386b4d32d0c0534ad970a68216594cc1d52e5e65447f7345cb0ac49db4c924a2`
- Per-class counts (train / val / test):
  - bug: 38 / 0 / 1
  - feature: 39 / 1 / 13
  - docs: 0 / 0 / 0
  - question: 1875 / 278 / 404

## Hyperparameters
- Freeze policy: full fine-tune
- LR: encoder 2e-5, classifier head 1e-4 (discriminative)
- Scheduler: linear warmup 10%, linear decay
- Weight decay: 0.01 (non-bias, non-LN)
- Batch size: 16
- Epochs: up to 5, early stop on val macro-F1, patience 1
- Tracking: TensorBoard (W&B blocked in user region; see DECISIONS.md)

## Metrics (test split)
- Accuracy: 0.9665
- Macro-F1: 0.3277
- Per-class F1: bug=0.0000, feature=0.0000, docs=0.0000, question=0.9830
- Per-class support: bug=1, feature=13, docs=0, question=404
- p50/p99 latency: TBD (measured in slice 04 baselines comparison)

## Weights
- Bucket / prefix: `s3://mc-models/classifier/v1/`
- Files: `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `spm.model`, `config.json`
- SHA-256 (model.safetensors): `1966ceac02bef9682998894fe01ba0536f6068700e0fc72ace268f967a423df1`
- Pinned in: `app/infra/_classifier_registry.py::WEIGHTS_SHA256`
- Boot check #5 compares the model-server-reported SHA against this pin.

## Limitations
- Trained on FastAPI-only English issues. No generalization claim beyond that corpus.
- Head-only truncation biases toward signal in the first 512 tokens.
- `<CODE>` placeholder strips lexical content of code blocks; the classifier cannot use code identifiers as features.
- Class imbalance: `docs` is severely under-represented in the time-stratified split (train support 0, test support 0); F1 on that class is not meaningful at this scale.
