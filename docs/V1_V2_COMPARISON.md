# V1 vs V2

| Dimension | V1 MVP | V2 Plugin |
| --- | --- | --- |
| Shape | Global `AGENTS.md` + user skill + CLI | Codex plugin + skill + MCP + CLI + profile |
| Discovery | Hardcoded global instruction | Plugin marketplace and installed skill |
| Config | Memory root embedded in instructions | `~/.personal-memory/profiles/default.json` |
| Retrieval | SQLite FTS direct top-k | FTS candidates + deterministic rerank |
| Broad memory | `conversations.md` could pollute top-k | Broad sources suppressed unless explicitly requested |
| Skill recall | Exact skill names could rank low | Exact skill names strongly boosted |
| Output | Summaries and paths | `score`, `reasons`, `read_next`, `suppressed_results`, `diagnostics` |
| Quality | Baseline eval report | Eval gate with regression thresholds |

## Measured Result On The Original 32-Case Suite

| Metric | V1 Baseline | V2 Optimized | Delta |
| --- | ---: | ---: | ---: |
| Cases | 32 | 32 | 0 |
| Recall@5 | 0.969 | 1.000 | +0.031 |
| MRR | 0.749 | 0.979 | +0.230 |
| Forbidden hits | 4 | 0 | -4 |
| skills Recall@5 | 0.667 | 1.000 | +0.333 |
| skills MRR | 0.133 | 1.000 | +0.867 |
| cwd_alias MRR | 0.698 | 1.000 | +0.302 |

## Optimization Idea

V2 keeps the implementation simple: use SQLite FTS to recall a broad candidate set, then use deterministic rules to rank and suppress. This removes noisy full-history files from primary context while keeping raw memory available for explicit history queries.

