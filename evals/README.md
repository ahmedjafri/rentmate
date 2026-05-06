# RentMate Evals

Use the dedicated local eval runner:

```bash
npm run evals -- --trials 3
npm run evals -- --case test_gas_smell
poetry run python -m evals run --out-dir eval-runs/manual
```

The runner repeats each selected eval, aggregates by pass rate, and writes
artifacts for every non-skipped trial.

Pytest capture is disabled by default, so `npm run evals` prints each agent
turn as it runs:

```text
[eval agent turn]
user: ...
agent: ...
```

Set `RENTMATE_EVAL_PRINT_AGENT_OUTPUT=0` to hide agent output while still
writing artifacts.

Default policy:

- `trials=3`
- case passes when at least `2/3` trials pass
- skipped pytest evals are ignored in aggregation

Artifact layout:

```text
<out-dir>/
  eval-results.jsonl
  summary.json
  <case-id>/
    trial-001/
      input.json
      output.json
      scores.json
      agent_runs.json
      atif_trajectory.json
      state_snapshots/
        turn-001.json
```

`agent_runs.json` includes AgentRun metadata, legacy AgentTrace rows, AgentStep
rows, and the serialized ATIF trajectory for each run.

Multi-actor evals also write full database snapshots after each completed turn.
Restore one into an isolated replay database and boot RentMate with background
startup tasks disabled:

```bash
poetry run python -m evals replay --run eval-runs/<run-folder> --trial 1 --turn 4 --port 8010
```
