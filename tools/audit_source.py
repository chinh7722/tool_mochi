"""Audit only Git's proposed source set, without reading excluded runtime secrets."""
import ast
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    git = sys.argv[1] if len(sys.argv) > 1 else "git"
    names = subprocess.check_output([git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
    names = sorted(set(filter(None, names)))
    failures = []
    secret = re.compile(r"(?:LOC-[a-zA-Z0-9]{10,}|sk-[a-zA-Z0-9_-]{20,}|\b\d{6,}:[A-Za-z0-9_-]{25,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----)")
    for name in names:
        path = root / name
        if path.suffix in (".exe", ".dll", ".pyd", ".db", ".sqlite3", ".zip", ".key", ".pem", ".csv") or any(part in ("data", "runtime", "scratch", "vps-data") for part in path.relative_to(root).parts):
            failures.append(name + ": runtime/private artifact")
            continue
        if path.suffix not in (".py", ".md", ".yml", ".sh", ".service", ".txt", ".bat", ".cmd", ".example"):
            continue
        source = path.read_text(encoding="utf-8-sig")
        if secret.search(source):
            failures.append(name + ": possible embedded credential")
        if path.suffix == ".py":
            try:
                ast.parse(source, filename=name)
            except SyntaxError:
                failures.append(name + ": Python syntax error")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Audited {len(names)} source/assets files. No matching private artifacts or embedded credentials.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
