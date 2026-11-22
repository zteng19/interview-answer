# Evaluation report (mock runs)

Target: mock runs – plumbing and code paths only; model quality needs a real model

| Scenario | Mode | Final state | Checks |
|---|---|---|---|
| disc_s3 | discovery | CARD_ISSUED | 11/11 |
| disc_s2_sponsor | discovery | CARD_ISSUED | 10/10 |
| disc_s4 | discovery | CARD_ISSUED | 8/8 |
| recall_mo03 | recall | CARD_ISSUED | 8/8 |
| neg_s5 | negative | NO_SCHEME | 2/2 |
| adv_injected_s2 | adversarial | NO_SCHEME | 12/12 |
| neg_s1 | negative | NO_SCHEME | 2/2 |
| neg_s2 | negative | NO_SCHEME | 2/2 |
| recall_mo06 | recall | CARD_ISSUED | 8/8 |
| recall_mo01 | recall | CARD_ISSUED | 8/8 |
| recall_mo02_mo07 | recall | CARD_ISSUED | 8/8 |

## Metrics

- scenarios_passed: 11/11
- oracle_checks_passed: 79/79
- final_state_accuracy: 11/11
- cards_schema_valid: 100% (cards are validated Pydantic models)
- manifest_chain_valid: True
