# ECS, ALB, Lambda, and alarms.

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${local.name}-gateway"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.data.arn
}

resource "aws_cloudwatch_log_group" "stream_processor" {
  name              = "/aws/lambda/${local.name}-stream-processor"
  retention_in_days = var.log_retention_days
}

locals {
  # Secrets are injected by ARN at container start. No secret value is ever
  # written to Terraform state or baked into the image.
  container_secrets = concat(
    var.openai_secret_name == "" ? [] : [{
      name      = "OPENAI_API_KEY"
      valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.openai_secret_name}"
    }],
    var.anthropic_secret_name == "" ? [] : [{
      name      = "ANTHROPIC_API_KEY"
      valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.anthropic_secret_name}"
    }],
    var.admin_key_secret_name == "" ? [] : [{
      name      = "ABC_ADMIN_API_KEY"
      valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.admin_key_secret_name}"
    }],
  )
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name}-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.gateway_cpu
  memory                   = var.gateway_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.gateway_task.arn

  container_definitions = jsonencode([{
    name      = "gateway"
    image     = local.image
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ABC_ENVIRONMENT", value = var.environment },
      { name = "ABC_AWS_REGION", value = var.aws_region },
      { name = "ABC_TABLE_CORE", value = aws_dynamodb_table.core.name },
      { name = "ABC_TABLE_LEDGER", value = aws_dynamodb_table.ledger.name },
      # Explicitly false: an in-memory budget store would lose every balance on
      # each deploy. The application also refuses to start this way in prod.
      { name = "ABC_USE_MEMORY_STORE", value = "false" },
      { name = "ABC_BEDROCK_ENABLED", value = tostring(var.enable_bedrock) },
      { name = "ABC_BEDROCK_REGION", value = var.aws_region },
      { name = "ABC_LANGFUSE_ENABLED", value = tostring(var.langfuse_host != "") },
      { name = "LANGFUSE_HOST", value = var.langfuse_host },
      # Prompt/response capture stays off: budget governance needs usage and
      # identity metadata, not conversation content.
      { name = "ABC_TRACE_CONTENT", value = "false" },
    ]

    secrets = local.container_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.gateway.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "gateway"
      }
    }

    # Liveness only. A health check that fails on a dependency outage would
    # restart-loop the task for a problem restarting cannot fix.
    healthCheck = {
      command     = ["CMD-SHELL", "curl -f -s http://localhost:8000/healthz || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 15
    }

    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
    }
  }])
}

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "prod"
}

resource "aws_lb_target_group" "gateway" {
  name        = "${local.name}-gateway"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }

  # Long enough for an in-flight provider call to finish and reconcile. Cutting
  # a task off mid-request leaves its reservation for the sweeper instead of
  # settling it against real usage.
  deregistration_delay = 60
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}

resource "aws_ecs_service" "gateway" {
  name            = "${local.name}-gateway"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.gateway_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.enable_nat_gateway ? aws_subnet.private[*].id : aws_subnet.public[*].id
    security_groups  = [aws_security_group.gateway.id]
    assign_public_ip = !var.enable_nat_gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 8000
  }

  # Rolling deploys keep the old tasks serving until the new ones are healthy,
  # so budget enforcement never has a gap.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 30
  depends_on                        = [aws_lb_listener.http]
}

# ---------------------------------------------------------------------------
# Stream processor
# ---------------------------------------------------------------------------
#
# handler.py imports abc_gateway.domain, abc_gateway.repo (the DynamoDB
# backend), abc_gateway.observability.logging and abc_gateway.runaway -- none
# of which exist on a bare Lambda runtime. Zipping apps/stream_processor/ alone
# (the previous version of this file) produced a function that fails at import
# on its first invocation; terraform validate cannot catch that because it
# never imports the Python it packages. See docs/FINDINGS.md and
# scripts/build_stream_processor_lambda.sh, which stages handler.py alongside
# a copy of abc_gateway and its pinned runtime dependencies (boto3, botocore,
# structlog, tzdata) before this data source zips the result.

locals {
  stream_processor_build_dir = "${path.module}/.build/stream_processor"
}

resource "null_resource" "stream_processor_build" {
  triggers = {
    handler_hash = filesha256("${path.module}/../../apps/stream_processor/handler.py")
    package_hash = sha1(join("", [
      for f in fileset("${path.module}/../../apps/gateway/src/abc_gateway", "**/*.py") :
      filesha1("${path.module}/../../apps/gateway/src/abc_gateway/${f}")
    ]))
    build_script_hash = filesha256("${path.module}/../../scripts/build_stream_processor_lambda.sh")
  }

  provisioner "local-exec" {
    command = "bash \"${path.module}/../../scripts/build_stream_processor_lambda.sh\" \"${local.stream_processor_build_dir}\""
  }
}

data "archive_file" "stream_processor" {
  type        = "zip"
  source_dir  = local.stream_processor_build_dir
  output_path = "${path.module}/.build/stream_processor.zip"

  depends_on = [null_resource.stream_processor_build]
}

resource "aws_lambda_function" "stream_processor" {
  function_name = "${local.name}-stream-processor"
  role          = aws_iam_role.stream_processor.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 512

  filename         = data.archive_file.stream_processor.output_path
  source_code_hash = data.archive_file.stream_processor.output_base64sha256

  environment {
    variables = {
      ABC_AWS_REGION   = var.aws_region
      ABC_TABLE_CORE   = aws_dynamodb_table.core.name
      ABC_TABLE_LEDGER = aws_dynamodb_table.ledger.name
      ABC_ALERT_TOPIC  = aws_sns_topic.alerts.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.stream_processor]
}

# Ledger stream: one clean financial event per record, which is what the
# runaway detector consumes.
resource "aws_lambda_event_source_mapping" "ledger" {
  event_source_arn  = aws_dynamodb_table.ledger.stream_arn
  function_name     = aws_lambda_function.stream_processor.arn
  starting_position = "LATEST"

  batch_size                         = 25
  maximum_batching_window_in_seconds = 2
  # Individual failures are reported rather than failing the whole batch, so one
  # poison record cannot stall detection indefinitely.
  function_response_types        = ["ReportBatchItemFailures"]
  maximum_retry_attempts         = 3
  bisect_batch_on_function_error = true
}

# Core stream: the threshold backstop, filtered to budget-state changes so
# unrelated writes cost nothing.
resource "aws_lambda_event_source_mapping" "core" {
  event_source_arn  = aws_dynamodb_table.core.stream_arn
  function_name     = aws_lambda_function.stream_processor.arn
  starting_position = "LATEST"

  batch_size                         = 25
  maximum_batching_window_in_seconds = 2
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_retry_attempts             = 3

  filter_criteria {
    filter {
      pattern = jsonencode({
        dynamodb = {
          NewImage = {
            entity_type = { S = ["BUDGET_STATE"] }
          }
        }
      })
    }
  }
}

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = aws_kms_key.data.id
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# A non-zero overage means a provider exceeded the hard output cap we sent it.
# That should never happen, so it pages rather than merely appearing on a
# dashboard.
resource "aws_cloudwatch_metric_alarm" "reconciliation_failures" {
  alarm_name          = "${local.name}-reconciliation-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  period              = 300
  statistic           = "Sum"
  namespace           = "AgentBudgetController"
  metric_name         = "budget_reconciliation_failures_total"
  treat_missing_data  = "notBreaching"
  alarm_description   = "A reservation could not be settled; money may be stuck in a held state."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "stream_processor_errors" {
  alarm_name          = "${local.name}-stream-processor-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 0
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Runaway detection is failing; agents could overspend unnoticed."
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.stream_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "stream_iterator_age" {
  alarm_name          = "${local.name}-stream-lag"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 120000 # 2 minutes in ms
  period              = 300
  statistic           = "Maximum"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Runaway detection is lagging behind actual spend."
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.stream_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "gateway_5xx" {
  alarm_name          = "${local.name}-gateway-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 10
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.gateway.arn_suffix
  }
}
