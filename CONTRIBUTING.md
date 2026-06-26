# Contributing

Contributions welcome!

## Development
```bash
pip install -r requirements.txt
python -m unittest discover -v      # 26 tests, external calls mocked
ruff check .
```

## Guidelines
- Keep dependencies minimal (stdlib + `requests`).
- Add a test for any logic change (`test_fleet_watch.py`).
- All DB access stays read-only; never weaken the read-only / timeout guards.
- Conventional Commits, English. Never commit secrets — use the `.env.example`.
