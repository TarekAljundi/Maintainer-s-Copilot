# Model Card — Classifier v1

## Architecture
- Base: `microsoft/deberta-v3-small` (44M params, 12 layers, 384 hidden)
- Head: linear over 4 classes (bug, feature, docs, question)
- Tokenizer: DebertaV2Tokenizer (sentencepiece), max length 512

## Training data
- Source: `pandas-dev/pandas` closed issues, >= 2020-01-01 (corpus swapped from fastapi/fastapi mid-project — see DECISIONS.md §Dataset)
- Label mapping: LABEL_MAP (`Bug`→bug, `Enhancement`→feature, `Docs`/`Documentation`→docs, `Usage Question`→question) + tiebreak `bug > feature > docs > question`
- Splits: 70/10/15 train/val/test, time-stratified by `closed_at` ascending
- Preprocessing: title+body, code blocks -> `<CODE>` placeholder, 512-tok head trunc
- Dataset manifest sha256: `335cdb888ffd0e809628646c4682f257676492e8e882d2928dbd21929d1f6bfd`
- Per-class counts (train / val / test):
  - bug: 2746 / 360 / 616
  - feature: 693 / 157 / 163
  - docs: 681 / 116 / 198
  - question: 518 / 29 / 16

## Hyperparameters
- Freeze policy: full fine-tune
- LR: encoder 2e-5, classifier head 1e-4 (discriminative)
- Scheduler: linear warmup 10%, linear decay
- Weight decay: 0.01 (non-bias, non-LN)
- Batch size: 16
- Epochs: up to 5, early stop on val macro-F1, patience 1
- Tracking: TensorBoard (W&B blocked in user region; see DECISIONS.md)

## Metrics (test split)
- Accuracy: 0.9517
- Macro-F1: 0.8959
- Per-class F1: bug=0.9686, feature=0.9483, docs=0.9167, question=0.7500
- Per-class support: bug=616, feature=163, docs=198, question=16
- p50/p99 latency: TBD (measured in slice 04 baselines comparison)

## Weights
- Bucket / prefix: `s3://mc-models/classifier/v1/`
- Files: `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `spm.model`, `config.json`
- SHA-256 (model.safetensors): `f4a5c67f8e72119a97270bb7a93a3657bcf9ee3db73cbe4f74123636261a0975`
- Pinned in: `app/infra/_classifier_registry.py::WEIGHTS_SHA256`
- Boot check #5 compares the model-server-reported SHA against this pin.

## Limitations
- Trained on pandas-only English issues. No generalization claim beyond that corpus.
- Head-only truncation biases toward signal in the first 512 tokens.
- `<CODE>` placeholder strips lexical content of code blocks; the classifier cannot use code identifiers as features.
- Class imbalance: `question` is the smallest class (train support 518, test support 16); per-class F1 on `question` carries higher variance than the other three classes.
