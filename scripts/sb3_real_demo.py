"""Real-framework validation: SB3 PPO on CartPole with the watchdog callback.

Not part of CI — requires ``pip install stable-baselines3 gymnasium torch``.
Attempt 1 trains until an external SIGTERM arrives: preemption_guard turns it
into a contract-valid ``finish("cancelled")`` and the process dies exactly as
the scheduler expects.  Attempt 2 is the retry — same (task, seed), new
run_id — and completes normally.  POST both event files to a running agent's
``/events`` endpoint to see the derived lineage (attempt ordinal, lost steps)
in one stream; the files are plain schema-v2 JSONL.

Example:
    python scripts/sb3_real_demo.py --out /tmp/sb3-demo/events-a1.jsonl --attempt 1
    kill -TERM <pid>                       # the platform preempts
    python scripts/sb3_real_demo.py --out /tmp/sb3-demo/events-a2.jsonl --attempt 2 --timesteps 40000
    curl -X POST --data-binary @/tmp/sb3-demo/events-a1.jsonl localhost:9102/events
    curl -X POST --data-binary @/tmp/sb3-demo/events-a2.jsonl localhost:9102/events
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Real SB3 CartPole run with the watchdog")
    parser.add_argument("--out", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()

    import torch

    torch.set_num_threads(args.threads)

    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env

    from sentinel_agent.integrations import sentinel_callback
    from sentinel_agent.resilience import preemption_guard

    callback = sentinel_callback(
        out_path=args.out, task="sb3-cartpole", seed="42", planned_steps=args.timesteps,
        emit_every_steps=256,
        producer_kwargs={"run_id": f"sb3-cartpole-42-a{args.attempt}"},
    )
    print(f"attempt {args.attempt}: pid={__import__('os').getpid()} "
          f"events={args.out}", flush=True)
    env = make_vec_env("CartPole-v1", n_envs=1, seed=42)
    model = PPO("MlpPolicy", env, seed=42, verbose=0)
    try:
        with preemption_guard(callback.producer):
            model.learn(total_timesteps=args.timesteps, callback=callback)
        print(f"attempt {args.attempt}: completed normally", flush=True)
    finally:
        callback.producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
