# IAM.
#
# The important policy in this file is the ledger one. The gateway is granted
# PutItem on the ledger table and *not* UpdateItem or DeleteItem, which turns
# "the usage ledger is immutable" from a convention someone could refactor away
# into a guarantee enforced by the platform. It is also the main reason the
# ledger lives in its own table: a single-table design cannot express it,
# because the same role must be able to update budget counters.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------------------
# Execution role: pulling the image and writing logs, nothing else
# ---------------------------------------------------------------------------

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role reads secrets only to inject them as environment variables
# at container start. The task role never gets this permission.
data "aws_iam_policy_document" "execution_secrets" {
  count = length(local.provider_secrets) > 0 ? 1 : 0

  statement {
    sid     = "ReadProviderSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      for name in local.provider_secrets :
      "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${name}-*"
    ]
  }

  statement {
    sid       = "DecryptSecrets"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(local.provider_secrets) > 0 ? 1 : 0

  name   = "${local.name}-execution-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets[0].json
}

# ---------------------------------------------------------------------------
# Task role: what the running gateway may do
# ---------------------------------------------------------------------------

resource "aws_iam_role" "gateway_task" {
  name               = "${local.name}-gateway-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "gateway_task" {
  # Full read/write on budget counters: reserving and settling both mutate them.
  statement {
    sid = "BudgetStateReadWrite"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:ConditionCheckItem",
      "dynamodb:TransactWriteItems",
      "dynamodb:TransactGetItems",
      "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.core.arn,
      "${aws_dynamodb_table.core.arn}/index/*",
    ]
  }

  # Append-only on the ledger. Note the deliberate absence of UpdateItem and
  # DeleteItem: even a compromised gateway, or a future code change that tried,
  # could not rewrite the record of what was spent.
  statement {
    sid = "LedgerAppendOnly"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.ledger.arn,
      "${aws_dynamodb_table.ledger.arn}/index/*",
    ]
  }

  statement {
    sid       = "ExplicitlyDenyLedgerMutation"
    effect    = "Deny"
    actions   = ["dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"]
    resources = [aws_dynamodb_table.ledger.arn]
  }

  statement {
    sid       = "EncryptDecryptData"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid = "WriteLogsAndMetrics"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gateway_task" {
  name   = "${local.name}-gateway-task"
  role   = aws_iam_role.gateway_task.id
  policy = data.aws_iam_policy_document.gateway_task.json
}

# Bedrock authenticates with this role directly, so a Bedrock-only deployment
# stores no provider API key at all.
data "aws_iam_policy_document" "bedrock" {
  count = var.enable_bedrock ? 1 : 0

  statement {
    sid     = "InvokeBedrockModels"
    actions = ["bedrock:InvokeModel", "bedrock:Converse", "bedrock:CountTokens"]
    resources = length(var.bedrock_model_arns) > 0 ? var.bedrock_model_arns : [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/*"
    ]
  }
}

resource "aws_iam_role_policy" "bedrock" {
  count = var.enable_bedrock ? 1 : 0

  name   = "${local.name}-bedrock"
  role   = aws_iam_role.gateway_task.id
  policy = data.aws_iam_policy_document.bedrock[0].json
}

# ---------------------------------------------------------------------------
# Stream processor role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "stream_processor" {
  name               = "${local.name}-stream-processor"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "stream_processor" {
  statement {
    sid = "ReadStreams"
    actions = [
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
      "dynamodb:DescribeStream",
      "dynamodb:ListStreams",
    ]
    resources = [
      aws_dynamodb_table.core.stream_arn,
      aws_dynamodb_table.ledger.stream_arn,
    ]
  }

  # The detector maintains rolling-spend buckets, flips threshold flags, and
  # pauses agents -- all on the core table.
  statement {
    sid = "MaintainDetectionState"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:TransactWriteItems",
    ]
    resources = [
      aws_dynamodb_table.core.arn,
      "${aws_dynamodb_table.core.arn}/index/*",
    ]
  }

  # Runaway events and audit records are appended, never edited.
  statement {
    sid       = "AppendAuditRecords"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.ledger.arn, "${aws_dynamodb_table.ledger.arn}/index/*"]
  }

  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    sid       = "Decrypt"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "stream_processor" {
  name   = "${local.name}-stream-processor"
  role   = aws_iam_role.stream_processor.id
  policy = data.aws_iam_policy_document.stream_processor.json
}
