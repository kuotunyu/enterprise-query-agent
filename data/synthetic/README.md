# Synthetic development data

base.json is the deterministic D01–D12 fixture. IDs are fixed 32-character hex strings. split_payment.json replaces a 100 BRL payment with 40 + 60 BRL; older_review.json adds an earlier score without changing the latest review. These are public development material, never holdout. scripts/bootstrap_data.py generates the same fixtures without downloading data or requiring API credentials. tests/oracle.py calculates independent Decimal gold values without importing the compiler or SQL.
