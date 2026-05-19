"""CI gate. Loads eval_report.json + eval_thresholds.yaml.

Pass iff for every metric: current >= floor AND current >= baseline - margin.
Exits non-zero on failure to block merge.
"""
