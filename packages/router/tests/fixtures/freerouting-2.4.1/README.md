# Captured scale regression

Generated 2026-09-15 from `test_specctra._problem()` through `write_dsn`, then
Freerouting 2.4.1, Temurin 25, `-mp 2 -mt 1`. The SES is the router output with trailing whitespace removed. Tests read it offline; they do not launch Java or access the network.

Known geometry: two horizontal 10 mm connections, at y=0 and y=5 mm, with
0.2 and 0.5 mm widths. DSN coordinates use 10,000 units/mm; this pinned
router returns 100,000 units/mm despite retaining `(resolution um 10)`.
This fixture covers this writer/router combination, not arbitrary SES files.
