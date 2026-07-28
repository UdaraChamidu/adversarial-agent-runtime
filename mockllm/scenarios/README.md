# Scenario format

Scenario files use JSON syntax, which is valid YAML 1.2. This keeps the scenario
data human-readable and machine-editable without adding a YAML parser dependency.

Each file declares:

- `id`: stable S1–S12 identifier;
- `behavior`: handler name in `mockllm.scenarios.ScenarioEngine`;
- `description`: the adversarial contract;
- `params`: data used by that handler.

Transport failures for S5, S6, and S12 are applied by the HTTP server after the
scenario engine has built a deterministic logical response.
