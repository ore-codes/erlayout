"""Reduce the public sample-database dumps to their DDL (tables + keys).
Sakila's monthly payment partitions are dropped."""
import re

from erlayout.parse import parse_sql

for name in ['chinook', 'sakila', 'northwind']:
    txt = open(f'schemas/{name}.sql', encoding='utf-8', errors='replace').read()
    txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.S)
    txt = re.sub(r'--[^\n]*', '', txt)
    keep = []
    for s in re.split(r';\s*\n', txt):
        m = re.search(r'(CREATE\s+TABLE|ALTER\s+TABLE)', s, re.I)
        if not m:
            continue
        s = s[m.start():].strip()
        if re.match(r'(CREATE|ALTER)\s+TABLE\s+(ONLY\s+)?(public\.)?payment_p\d', s, re.I):
            continue
        if s.upper().startswith('CREATE') or 'FOREIGN KEY' in s.upper() or 'PRIMARY KEY' in s.upper():
            keep.append(s)
    ddl = ';\n'.join(keep) + ';\n'
    open(f'schemas/{name}_ddl.sql', 'w').write(ddl)
    sc = parse_sql(ddl, 'postgres')
    print(name, len(sc.tables), len(sc.edges), len(sc.solver_edges()))
