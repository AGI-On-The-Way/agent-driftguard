# Scripts

Run the rollback scenario and regenerate all dashboard data:

```bash
python3 scripts/run_demo.py
```

Run the verified-improvement branch:

```bash
python3 scripts/run_demo.py --scenario keep
```

The runner imports the bundled `src/feedback_kit` package and requires only
Python's standard library.
