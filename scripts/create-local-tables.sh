#!/bin/sh
# Create the DynamoDB tables for local development.
#
# Reads the same infra/table_*.json specs the test suite and Terraform use, so
# a local stack cannot drift from what is tested or what gets deployed.
set -eu

ENDPOINT="${ABC_DYNAMODB_ENDPOINT_URL:-http://dynamodb:8000}"

create_table() {
  spec_file="$1"
  table_name=$(sed -n 's/.*"TableName"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$spec_file" | head -1)

  if aws dynamodb describe-table \
        --endpoint-url "$ENDPOINT" \
        --table-name "$table_name" >/dev/null 2>&1; then
    echo "table $table_name already exists"
    return 0
  fi

  # Strip the "_comment" documentation keys; CreateTable rejects unknown fields.
  python3 - "$spec_file" > /tmp/spec.json <<'PY'
import json, sys

def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value

spec = clean(json.load(open(sys.argv[1])))
# TTL is configured separately, after the table exists.
spec.pop("TimeToLiveSpecification", None)
json.dump(spec, sys.stdout)
PY

  echo "creating table $table_name"
  aws dynamodb create-table \
    --endpoint-url "$ENDPOINT" \
    --cli-input-json "file:///tmp/spec.json" >/dev/null

  aws dynamodb wait table-exists \
    --endpoint-url "$ENDPOINT" \
    --table-name "$table_name"
  echo "table $table_name ready"
}

create_table /infra/table_core.json
create_table /infra/table_ledger.json
echo "local budget store ready"
