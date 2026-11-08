#!/usr/bin/env python3
"""Ломание по списку: подмена в копии репозитория во временной папке, тесты, число упавших против ожидания.

Рабочее дерево на запись не открывается. Образец обязан встречаться в файле ровно один раз. Копия до подмен обязана
быть зелёной. Список — JSON [{"file", "old", "new", "expect", "why"}].
    .venv/bin/python scripts/break_check.py tests/breaks.json
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
SKIP = shutil.ignore_patterns(".venv", ".git", "__pycache__", ".pytest_cache")


def run(tree: Path) -> tuple[int, str, list[str]]:
    r = subprocess.run([PY, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", "tests"], cwd=tree,
                       capture_output=True, text=True, env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    m = re.search(r"(\d+) failed", r.stdout)
    names = [ln.split()[1].split("::")[-1] for ln in r.stdout.splitlines() if ln.startswith("FAILED ")]
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    return (int(m.group(1)) if m else 0), last, names


def main() -> int:
    cases = json.loads(Path(sys.argv[1]).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=SKIP)
        failed, line, _ = run(base)
        if failed or "passed" not in line:
            print(f"копия красная до подмен: {line} — вердиктов нет")
            return 2
        print(f"копия зелёная: {line}\n")
        bad = 0
        for i, c in enumerate(cases, 1):
            tree = Path(tmp) / f"t{i}"
            shutil.copytree(base, tree)
            f = tree / c["file"]
            text = f.read_text()
            n = text.count(c["old"])
            if n != 1:
                print(f"{i:2}. ОБРАЗЕЦ {n} раз(а) — пропуск: {c['why']}")
                bad += 1
                continue
            f.write_text(text.replace(c["old"], c["new"]))
            got, line, names = run(tree)
            ok = got == c["expect"]
            bad += not ok
            verdict = f"ЛОВИТ ({got})" if got else "НЕ ЛОВИТ"
            print(f"{i:2}. {verdict:<12} ждали {c['expect']}{'' if ok else '  ← РАСХОЖДЕНИЕ'}  {c['why']}")
            if not ok:
                print("      упали: " + ", ".join(names))
            shutil.rmtree(tree)
        print(f"\nрасхождений с ожиданием: {bad}")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
