# Sample schemas

PostgreSQL versions of three public sample databases, cut down to their
tables, primary keys and foreign keys:

| File | Tables / foreign keys drawn |
| --- | --- |
| `chinook.sql` | 11 / 10 |
| `northwind.sql` | 14 / 12 |
| `sakila.sql` | 15 / 22 (the monthly `payment_p*` partitions are left out) |

`sakila.sql` is also the web interface's example (`erlayout/static/example.sql`);
the two files are identical. `bench/run_bench.py named` lays these out for the
results table in the README.
