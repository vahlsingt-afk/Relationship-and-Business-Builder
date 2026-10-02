# Ecosystem Intelligence Inbox

Drop operator-supplied ecosystem files here before ingestion.

Suggested layout:

- `technomic_top_1500_2024.xlsx`
- `technomic_top_1500_2025.xlsx`
- `vendor_evidence_pos_YYYY-MM-DD.csv`
- `vendor_evidence_payments_YYYY-MM-DD.csv`

Ingestion commands:

```bash
python3 system/scripts/ecosystem_intelligence.py ingest-restaurants system/inbox/ecosystem/technomic_top_1500_2024.xlsx --dry-run
python3 system/scripts/ecosystem_intelligence.py ingest-restaurants system/inbox/ecosystem/technomic_top_1500_2024.xlsx
python3 system/scripts/ecosystem_intelligence.py ingest-vendors system/inbox/ecosystem/vendor_evidence_pos_YYYY-MM-DD.csv --dry-run
python3 system/scripts/ecosystem_intelligence.py ingest-vendors system/inbox/ecosystem/vendor_evidence_pos_YYYY-MM-DD.csv
```

Vendor-category uploads are evidence leads, not final truth. RB should mark them
`evidence_posture: provisional` unless corroborated. In restaurant POS especially,
separate system-of-record POS, approved hardware, payment/acquiring, regional
deployment, franchisee deployment, pilot, legacy incumbent, and replacement signal.
