# Domain regression source

This directory preserves the platform's domain tests and pytest configuration. The integration configuration requires a guarded loopback disposable PostgreSQL database named `aislemint_test`; it rejects deployed databases. Historical benchmark-oriented tests additionally require their original public benchmark asset fixtures. These are not supplied by a customer export.

For the self-contained hackathon simulation, first use the database-free tests in `hackathon/tests`, then run `python -m hackathon.verify /protected/demo.env` against a newly seeded dedicated demo database. That verifier exercises actual product routes with two independent synthetic guests.
