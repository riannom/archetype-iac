# Image Rules

Place a JSON file at `WORKSPACE/images/rules.json` to add regex-based device detection.

Example:

```json
{
  "rules": [
    { "pattern": "cloud?eos", "device_id": "eos" },
    { "pattern": "csr", "device_id": "csr" }
  ]
}
```

Rules are applied before built-in filename keyword detection.
