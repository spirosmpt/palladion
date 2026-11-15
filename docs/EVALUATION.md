# Evaluation

## Sets

| Set | File | Measures |
|---|---|---|
| Development | `eval/dev/mati_2018_gold.yaml` (the 40 messages in `scenarios/mati_2018/messages.yaml`) | decision, location, people, duplicates |
| Field reports | `eval/dev/mati_2018_field_gold.yaml` | reading crew reports and what code does with them |
| Adversarial | `eval/dev/mati_2018_adversarial.yaml` (25 messages) | nothing breaks, every message reaches a person |
| Sealed | `eval/sealed/mati_2018_sealed.yaml`, written from the template and never committed | the same as development, on messages the code has not seen |
| Red team | `eval/redteam/regression.jsonl` | misleading messages that once failed and were approved as tests |

The agents never read expected answers.

## Results

| Set | Messages | Decision | Dangerous errors | Location | People | Model |
|---|---|---|---|---|---|---|
| **Sealed** | 33 | **33/33** | **0** | 23/26 | 26/26 | gemini-flash-lite-latest |
| Development | 40 | 40/40 | – | 33/36 | – | gemini-3.5-flash-lite (Vertex AI) |
| Development, no model (`--oracle`) | 40 | 40/40 | – | 35/36 | 20/20 | none: location and rules only |
| Adversarial | 25 | 25/25 without errors | – | – | – | with and without a model |
| Red team | 2 tests | 2/2 pass | – | – | – | – |

The sealed set is the number that counts: the code's rules were tuned on the development set, so that result is
optimistic. Two development messages (m01, m15) accept "shelter in place" or "send a unit" as well as evacuation:
the front is 7′ away and no vehicle route reaches a safe zone with a 10′ margin.

**Dangerous error:** the desk said "safe", "information" or "monitor" when someone should have moved. This must
stay at 0.

**Known location limit:** some streets are missing from OpenStreetMap; there the desk asks the sender instead of
guessing.

## Commands

```bash
python -m palladion.eval.run_dev              # development set with the model
python -m palladion.eval.run_dev --oracle     # without a model
python -m palladion.eval.run_sealed           # sealed set, prints a summary only
python -m palladion.eval.run_adversarial      # adversarial set (--live with a model)
python -m palladion.redteam.run --n 15        # a new red-team round
python -m palladion.redteam.run --replay      # re-run the approved regressions
```
