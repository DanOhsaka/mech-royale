# ZeroByte — Mech Royale bot

Autonomous mech controller for the Builders Buildathon Mech Royale track.

## Basis and credits

This bot is adapted from the open-source controller **`LanceHuang245/mech-royale`**
(MIT license), with changes by **ZeroByte / Chukwuebuka Umeh**. It also uses the
official Builders `mechfight-kit` Python wrapper (`mechfight.py`, MIT). The
original copyright notices are retained in `LICENSE`.

Changes in this fork are tracked in the git history. Current changes:

- `/arena` fallback when the conformance checker rewrites `hello.map.dir` with
  host path rules.
- Tuning and evaluation work by ZeroByte.

## Run locally

```sh
python check/check.py --map-dir /path/to/mechfight-kit/arena -- python -u bot.py
```

## Docker

```sh
docker build -t zerobyte-bot .
docker run --rm -i --network none --cpus 1 --memory 512m --read-only \
  --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "/path/to/mechfight-kit/arena:/arena:ro" zerobyte-bot
```

## License

MIT. See `LICENSE` for the original and fork copyright notices.
