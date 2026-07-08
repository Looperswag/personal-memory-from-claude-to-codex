# Memory Query Optimized Eval

Date: 2026-07-08

## Command

```bash
python3 scripts/eval_memory_query.py --top-k 5 --profile default
```

## Score

| Metric | Value |
| --- | ---: |
| Cases | 32 |
| Recall@5 | 1.000 (32/32) |
| MRR | 0.979 |
| Forbidden hits | 0 |

## Category Breakdown

| Category | Cases | Recall@5 | MRR | Forbidden |
| --- | ---: | ---: | ---: | ---: |
| alias_language | 2 | 1.000 | 0.667 | 0 |
| cwd_alias | 8 | 1.000 | 1.000 | 0 |
| explicit_query | 13 | 1.000 | 1.000 | 0 |
| profile | 3 | 1.000 | 1.000 | 0 |
| safety_budget | 3 | 1.000 | 1.000 | 0 |
| skills | 3 | 1.000 | 1.000 | 0 |

## Gate Result

Passed.

Remaining watch item: `alias_language` MRR is lower than the other categories. This is the next natural target if the eval set expands around English aliases.

