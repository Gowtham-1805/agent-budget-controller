# Infrastructure

Terraform for the deployed system: VPC, ECS Fargate, ALB, two DynamoDB tables,
a Lambda stream processor, IAM, KMS, and CloudWatch alarms.

See [../../docs/DATA_MODEL.md](../../docs/DATA_MODEL.md) for why the schema
here is defined once in `infra/table_*.json` and consumed by both this
configuration and the test suite — not duplicated between them.

**This has never been applied.** No AWS credentials were available during
development. `terraform validate` and `terraform fmt -check` both pass; nothing
beyond that has been verified against a real account. See
[../../docs/PROJECT_CONTEXT.md #what-has-not-been-proven](../../docs/PROJECT_CONTEXT.md#what-has-not-been-proven).

---

## What gets created

| File | Resources |
|---|---|
| `main.tf` | VPC, subnets (public + optional private), NAT, IGW, security groups, both DynamoDB tables, KMS key, ECR repository |
| `compute.tf` | ECS cluster/service/task, ALB + target group + listener, the Lambda stream processor + its two event source mappings, SNS alerts topic, CloudWatch alarms |
| `iam.tf` | Execution role (image pull + log write only), task role (budget read/write, ledger **append-only** — see below), stream processor role |
| `variables.tf` | Every input, documented inline |
| `outputs.tf` | Gateway URL, table names, log group names, a cost reminder |

### The IAM detail worth understanding before touching this

The gateway's task role is granted `PutItem`, `GetItem`, `Query`, and
`TransactWriteItems` on the ledger table — but **not** `UpdateItem` or
`DeleteItem`, with an explicit `Deny` statement on top for good measure. This is
what turns "the usage ledger is immutable" from a code convention into a
platform-enforced guarantee, and it's a large part of why the ledger lives in
its own DynamoDB table rather than sharing one with the budget counters. See
[../../docs/DATA_MODEL.md #two-tables-and-why](../../docs/DATA_MODEL.md#two-tables-and-why).

---

## Variables that matter

| Variable | Default | Notes |
|---|---|---|
| `aws_region` | `us-east-1` | Single authoritative region — see [DECISIONS.md #D12](../../docs/DECISIONS.md#d12-single-authoritative-write-region) |
| `environment` | `dev` | One of `dev`/`staging`/`prod`. Validated. |
| `enable_nat_gateway` | `true` | **The main cost lever.** `false` runs Fargate tasks in public subnets behind a restrictive security group instead of a NAT gateway |
| `gateway_desired_count` | `2` | Two tasks is meaningful beyond availability — it exercises the concurrency guarantee across real processes, not just threads |
| `enable_bedrock` | `false` | Grants the task role `bedrock:InvokeModel`/`Converse`/`CountTokens`. Bedrock needs no stored API key — it authenticates via this same role |
| `openai_secret_name` / `anthropic_secret_name` | `""` (unset) | Secrets Manager secret **names**, resolved at container start by the execution role. No secret value ever enters Terraform state |
| `point_in_time_recovery` | `true` | On both tables. This is financial state and the ledger is the record of record — leave this on |
| `container_image` | `""` (uses the created ECR repo) | Override to point at a pre-built image |

Full list with descriptions: `variables.tf`.

---

## Cost

**Real money if left running.** Roughly $60–90/month with defaults:

| Component | Cost |
|---|---|
| ALB | ~$16/month + LCU charges |
| 2× Fargate task (0.5 vCPU / 1GB) | ~$26–36/month |
| 2× NAT gateway (one per AZ) | ~$64/month + data processing |
| DynamoDB on-demand | ~$0 when idle |
| KMS key | ~$1/month |

Set `enable_nat_gateway = false` to drop the NAT cost entirely — Fargate tasks
run in public subnets with a public IP, locked down by a security group that
only admits traffic from the ALB. Materially cheaper, acceptable for a
demo/dev deployment where the tasks hold no data of their own.

The `estimated_monthly_cost_note` output prints a live estimate based on the
variables you actually set.

---

## Usage

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply       # creates billable resources
```

After applying:

```bash
terraform output gateway_url
terraform output ecr_repository_url    # push your built image here
```

```bash
terraform destroy     # when finished
```

Both DynamoDB tables carry `lifecycle { prevent_destroy = true }` — destroying
live budget state and the immutable ledger requires deliberately removing that
guard first, not a side effect of an ordinary `destroy`.

---

## Region strategy

Deliberately single-region. DynamoDB transactions are atomic only within the
region where they originate — global tables do not extend that guarantee, so
active-active budget mutation across two regions could let each authorise
spend against the same remaining balance independently. See
[DECISIONS.md #D12](../../docs/DECISIONS.md#d12-single-authoritative-write-region)
for the full reasoning and what a genuine multi-region version would need.

---

## Further reading

- [../../docs/OPERATIONS.md](../../docs/OPERATIONS.md) — the full deploy runbook and smoke test
- [../../docs/DATA_MODEL.md](../../docs/DATA_MODEL.md) — the DynamoDB schema these resources create
