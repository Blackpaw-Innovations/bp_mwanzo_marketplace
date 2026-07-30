# Security Policy

## Disclosure
- Email: security@blackpawinnovations.com (or per company policy)
- Response time expectation: 72 hours for critical reports.

## Threat Model
- Access is limited to defined groups (`bp_module_user`, `bp_module_manager`).
- Controller endpoints follow the Post-Development SOP for route classification.
- Attachments and exports honor record rules; background jobs do not use `sudo()` indiscriminately.

## Deployment Guardrails
- Always follow `BP_SOPs/ULTIMATE_DEPLOYMENT_PROTOCOL.md`.
- Ensure `security/ir.model.access.csv` lists every custom model with read/write restrictions.

Document any exceptions to this policy within this file before shipping.
