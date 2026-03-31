# LLM-MM-Agent Documentation

This `docs/` tree is a source-code-first tutorial set for understanding how MM-Agent actually works.

## Available now

- English docs: [English documentation portal](en/README.md)

## Planned in Phase 2

- Chinese docs will be added in the follow-up commit on branch `doc-260401`.

## Documentation map

| Path | Purpose |
| --- | --- |
| `docs/en/README.md` | Fast orientation and reading order |
| `docs/en/quick-start.md` | Installation, runtime commands, output layout |
| `docs/en/architecture.md` | System-level architecture and component responsibilities |
| `docs/en/workflow.md` | Step-by-step execution pipeline from prompt to artifacts |
| `docs/en/math-theory.md` | The fixed algorithms and formulas implemented in code |
| `docs/en/source-guide.md` | Where to read in the repository and why |
| `docs/en/evaluation.md` | MM-Bench structure and evaluation workflow |

## Why this docs tree exists

The repository already explains the project at a product level, but the runtime logic is spread across `MMAgent/main.py`, stage utilities, agent classes, the HMML library, and the MM-Bench evaluation scripts. This docs tree stitches those pieces into one learning path.

## Primary source anchors

- `MMAgent/main.py`
- `MMAgent/utils/problem_analysis.py`
- `MMAgent/utils/mathematical_modeling.py`
- `MMAgent/utils/computational_solving.py`
- `MMAgent/agent/coordinator.py`
- `MMAgent/agent/retrieve_method.py`
- `MMAgent/agent/task_solving.py`
- `MMAgent/HMML/HMML.md`
- `MMBench/README.md`
- `MMBench/evaluation/run_evaluation.py`
- `MMBench/evaluation/run_evaluation_batch.py`
