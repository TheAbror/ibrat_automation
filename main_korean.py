"""Same runner as main.py, pointed at the Korean course instead of English.

Opens "Koreys tili B1" (Muqaddas Taylanova) rather than "Ingliz tili B2",
and reads/writes result_korean.json instead of results.json, so answers
learned in one language never leak into another's answer key.

Usage: python3 main_korean.py — supervised, self-healing run (via supervisor.py).
       python3 main_korean.py --worker — this bare runner (needs your own Appium).
"""
import os

os.environ.setdefault("IBRAT_COURSE_DESCRIPTION", "Koreys tili B1\nMuqaddas Taylanova")
os.environ.setdefault("IBRAT_MODULE_PREFIX", "B1 |")
os.environ.setdefault("IBRAT_RESULTS_FILE", "result_korean.json")

import sys

import main as runner

if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.exit(runner.main())
    import supervisor
    sys.exit(supervisor.run(
        worker_cmd=[sys.executable, "-u", "main_korean.py", "--worker"]
    ))
