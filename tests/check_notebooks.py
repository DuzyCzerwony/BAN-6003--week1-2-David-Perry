#!/usr/bin/env python3
"""Lightweight local notebook checks for BAN 6003.

This checker intentionally grades completion and reproducibility, not analytical quality.
It verifies that required notebooks/files exist, notebooks can execute when possible,
student-marked code cells are not empty placeholders, marked cells have outputs after
execution when execution is available, and required markdown response cells were edited.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'assignment_config.json'

PLACEHOLDER_PHRASES = [
    'type your answer here',
    'write your answers here',
    'your answer here',
    'your response here',
    '[business problem]',
    '[data source]',
    '[profiling/cleaning/transformation/integration steps]',
    '[unit of analysis]',
    '[model or analytical method]',
    '[target or outcome]',
    '[key result]',
    '[business use]',
    '[main limitation]'
]

CODE_MARKERS = ['Your Turn', 'Your code here', 'your code here', 'TODO', 'Exercise']
SQL_RE = re.compile(r"query\s*=\s*([\"']{3})(.*?)(\1)", re.S | re.I)


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def fail(message: str) -> None:
    print(f'FAIL: {message}')
    raise SystemExit(1)


def strip_comments_and_blanks(source: str) -> str:
    useful = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        useful.append(line)
    return '\n'.join(useful).strip()


def meaningful_code(source: str) -> bool:
    useful = strip_comments_and_blanks(source)
    if not useful:
        return False
    # Empty SQL query templates count as incomplete even if pd.read_sql is present.
    for m in SQL_RE.finditer(source):
        sql = m.group(2).strip()
        if len(sql) < 12 or not re.search(r'\bselect\b', sql, re.I):
            return False
    return True


def has_marker(source: str) -> bool:
    return any(marker in source for marker in CODE_MARKERS)


def code_cell_should_have_output(source: str) -> bool:
    useful = strip_comments_and_blanks(source)
    if not useful:
        return False
    silent_patterns = ['to_csv(', 'to_excel(', 'mkdir(', 'write_text(', 'savefig(', 'plt.savefig']
    if any(p in useful for p in silent_patterns):
        return False
    return True


def check_no_forbidden_files(config: dict) -> None:
    for rel in config.get('forbidden_files', []):
        if (ROOT / rel).exists():
            fail(f'Forbidden file is present and should not be committed: {rel}')


def execute_notebook(path: Path, timeout: int) -> None:
    print(f'Executing {path.relative_to(ROOT)}')
    cmd = [
        sys.executable, '-m', 'nbconvert', '--execute', '--to', 'notebook', '--inplace',
        '--ExecutePreprocessor.timeout=' + str(timeout), str(path)
    ]
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        fail(f'Notebook did not execute cleanly: {path.relative_to(ROOT)}')


def check_notebook(path: Path, config: dict, executed: bool) -> None:
    nb = json.loads(path.read_text(encoding='utf-8'))
    cells = nb.get('cells', [])
    marked_code = 0
    checked_outputs = 0
    markdown_placeholders = []

    for idx, cell in enumerate(cells):
        ctype = cell.get('cell_type')
        source = ''.join(cell.get('source', ''))
        lower = source.lower()

        if ctype == 'markdown' and config.get('check_markdown_placeholders', True):
            # Only enforce placeholders in response-looking cells, not instructional examples.
            response_like = any(x in lower for x in ['your answer', 'draft', 'summary:', 'business problem:', 'data sources:', 'recommendation'])
            if response_like and any(p in lower for p in PLACEHOLDER_PHRASES):
                markdown_placeholders.append(idx)

        if ctype == 'code' and has_marker(source):
            marked_code += 1
            if not meaningful_code(source):
                fail(f'Marked code cell {idx} in {path.relative_to(ROOT)} still looks incomplete.')
            if executed and config.get('require_marked_code_outputs', True) and code_cell_should_have_output(source):
                checked_outputs += 1
                if not cell.get('outputs'):
                    fail(f'Marked code cell {idx} in {path.relative_to(ROOT)} has no output after execution.')

    min_marked = config.get('min_marked_code_cells', {}).get(str(path.relative_to(ROOT)), 0)
    if marked_code < min_marked:
        fail(f'{path.relative_to(ROOT)} has {marked_code} marked code cells, expected at least {min_marked}.')

    if markdown_placeholders:
        fail(f'Markdown response placeholders remain in {path.relative_to(ROOT)} cells {markdown_placeholders}.')

    print(f'Checked {path.relative_to(ROOT)}: {marked_code} marked code cells; {checked_outputs} output checks.')


def main() -> None:
    config = load_config()
    check_no_forbidden_files(config)

    for rel in config.get('required_files', []):
        if not (ROOT / rel).exists():
            fail(f'Missing required file: {rel}')

    requires_database = config.get('requires_database', False)
    execute = config.get('execute_notebooks', True)
    if requires_database and not os.environ.get('DATABASE_URL'):
        print('DATABASE_URL secret not available; skipping execution for database notebooks and running static completeness checks only.')
        execute = False

    timeout = int(config.get('execution_timeout_seconds', 600))
    executed = False
    if execute:
        for rel in config.get('notebooks', []):
            execute_notebook(ROOT / rel, timeout)
        executed = True

    for rel in config.get('notebooks', []):
        path = ROOT / rel
        if not path.exists():
            fail(f'Missing notebook: {rel}')
        check_notebook(path, config, executed)

    print('PASS: Notebook runtime/completeness checks passed.')


if __name__ == '__main__':
    main()
