output "gateway_url" {
  description = "Base URL of the deployed budget gateway. Point agents here."
  value       = "http://${aws_lb.main.dns_name}"
}

output "healthz_url" {
  description = "Liveness probe URL, for the deployment acceptance check."
  value       = "http://${aws_lb.main.dns_name}/healthz"
}

output "readyz_url" {
  description = "Readiness probe URL, for the deployment acceptance check."
  value       = "http://${aws_lb.main.dns_name}/readyz"
}

output "ecr_repository_url" {
  description = "Push the gateway image here before deploying."
  value       = aws_ecr_repository.gateway.repository_url
}

output "core_table_name" {
  description = "Budget-state table. Inspect it to prove state is persistent."
  value       = aws_dynamodb_table.core.name
}

output "ledger_table_name" {
  description = "Immutable usage ledger."
  value       = aws_dynamodb_table.ledger.name
}

output "gateway_log_group" {
  description = "CloudWatch log group carrying correlated request_id records."
  value       = aws_cloudwatch_log_group.gateway.name
}

output "stream_processor_log_group" {
  description = "Runaway detector logs."
  value       = aws_cloudwatch_log_group.stream_processor.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Force a new deployment against this to prove state survives a restart."
  value       = aws_ecs_service.gateway.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "estimated_monthly_cost_note" {
  description = "A reminder that this deployment is not free."
  value = join(" ", [
    "ALB ~$16/mo,",
    "Fargate ~$${var.gateway_desired_count * 13}/mo,",
    var.enable_nat_gateway
    ? "NAT ~$${var.availability_zone_count * 32}/mo (set enable_nat_gateway=false to avoid),"
    : "no NAT (tasks in public subnets behind a restrictive security group),",
    "DynamoDB on-demand is ~$0 when idle.",
    "Run 'terraform destroy' when finished.",
  ])
}
