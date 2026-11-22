# ZeroByte — Mech Royale bot

Autonomous mech controller for the Builders Buildathon Mech Royale track.

## What it is

A bot that speaks `mechfight/1` over stdin/stdout: it reads observations 10 times per match-second and returns movement, aim, and weapon decisions. It is built on the official starter wrapper (`mechfight.py`, included) and the official map files are mounted at `/arena` during matches.

## Run locally

```sh
python check/check.py -- python -u bot.py
```

(From a checkout of the official `mechfight-kit`, pointing at this file.)

## Docker

```sh
docker build -t zerobyte-bot .
docker run --rm -i --network none --cpus 1 --memory 512m --read-only \
  --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/../arena:/arena:ro" zerobyte-bot
```

## Credits

Starter wrapper and protocol kit: Builders Buildathon (`mechfight-kit`), MIT licensed. Bot logic: ZeroByte.
