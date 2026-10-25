# Verification and completion

Define acceptance criteria before implementation. Use the repository's actual
build and test commands. Report command, directory, exit status, outcome and counts;
zero tests are not a successful test suite. Diagnose failures instead of hiding them.

Record review findings and their disposition. If independent review is unavailable,
state that limitation. A manifest hash detects local changes, not trustworthiness.
Markdown guidance does not enforce runtime permissions or automatically block stopping.
Do not add a stop hook that always reports success or loops indefinitely.
