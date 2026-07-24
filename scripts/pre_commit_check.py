#!/usr/bin/env python3
"""Pre-commit validation script: Run coverage and pr-review in isolated subprocess.

This script runs in its own context and doesn't pollute the main session.
Returns 0 on success, 1 on failure (blocks commit on failure).
"""

import subprocess
import sys
from pathlib import Path

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
CHECKMARK = "✅"
CROSS = "❌"

def run_command(cmd: list, description: str) -> bool:
    """Run a command and return success status."""
    print(f"\n{YELLOW}🔍 {description}...{RESET}")
    try:
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if result.returncode == 0:
            print(f"{GREEN}{CHECKMARK} {description} passed{RESET}")
            return True
        else:
            print(f"{RED}{CROSS} {description} failed{RESET}")
            if result.stdout:
                print("\nOutput:")
                print(result.stdout)
            if result.stderr:
                print("\nErrors:")
                print(result.stderr)
            return False

    except subprocess.TimeoutExpired:
        print(f"{RED}{CROSS} {description} timed out (>10 minutes){RESET}")
        return False
    except Exception as e:
        print(f"{RED}{CROSS} Error running {description}: {e}{RESET}")
        return False

def main() -> int:
    """Run all pre-commit checks."""
    print(f"\n{YELLOW}═══════════════════════════════════════{RESET}")
    print(f"{YELLOW}  Pre-Commit Validation (Isolated){RESET}")
    print(f"{YELLOW}═══════════════════════════════════════{RESET}")

    checks = [
        (["make", "coverage"], "Coverage check (80% minimum)"),
        (["make", "pr-review"], "PR review checks"),
    ]

    results = []
    for cmd, description in checks:
        results.append(run_command(cmd, description))

    print(f"\n{YELLOW}═══════════════════════════════════════{RESET}")
    if all(results):
        print(f"{GREEN}✨ All pre-commit checks passed!{RESET}")
        print(f"{YELLOW}═══════════════════════════════════════{RESET}\n")
        return 0
    else:
        print(f"{RED}❌ Pre-commit checks failed!{RESET}")
        print("Fix the issues above and try committing again.")
        print(f"{YELLOW}═══════════════════════════════════════{RESET}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
