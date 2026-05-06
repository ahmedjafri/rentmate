import argparse
import json

from evals.replay import _resolve_snapshot_path


def test_resolve_snapshot_path_accepts_run_folder(tmp_path):
    run = tmp_path / "20260505-225845-case"
    snapshot = run / "trial-003" / "state_snapshots" / "turn-004.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({"schema": "rentmate.eval_state_snapshot.v1"}))

    assert _resolve_snapshot_path(str(run), 3, 4) == snapshot


def test_replay_parser_contract():
    from evals.replay import build_parser

    args = build_parser().parse_args([
        "--run",
        "20260505-225845-case",
        "--trial",
        "1",
        "--turn",
        "4",
        "--no-server",
    ])

    assert isinstance(args, argparse.Namespace)
    assert args.run == "20260505-225845-case"
    assert args.trial == 1
    assert args.turn == 4
    assert args.no_server is True
