variable "aws_region" {
  description = <<-EOT
    The single authoritative region for budget state.

    This is deliberately one region, not a global table. DynamoDB transactions
    are atomic only within the region where they originate, so active-active
    budget mutation across regions would allow two regions to independently
    authorise the same remaining dollars. Multi-AZ within one region gives
    durability without breaking the guarantee.
  EOT
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "project_name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "abc"
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zone_count" {
  description = "Number of AZs to span. Two is the minimum for a highly available ALB."
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Run Fargate tasks in private subnets behind a NAT gateway.

    true  - production posture: tasks have no public IP and egress via NAT.
            Costs roughly $32/month per NAT gateway plus data processing.
    false - demo posture: tasks run in public subnets with a public IP, locked
            down by security group. Materially cheaper, and acceptable when the
            tasks hold no data and the security group only admits the ALB.

    VPC endpoints for DynamoDB and S3 are created either way, so budget-store
    traffic never leaves the AWS network regardless of this setting.
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

variable "container_image" {
  description = "Gateway image URI. Defaults to the ECR repository created here."
  type        = string
  default     = ""
}

variable "gateway_cpu" {
  description = "Fargate CPU units for the gateway task."
  type        = number
  default     = 512
}

variable "gateway_memory" {
  description = "Fargate memory (MiB) for the gateway task."
  type        = number
  default     = 1024
}

variable "gateway_desired_count" {
  description = <<-EOT
    Number of gateway tasks.

    Two or more is meaningful here beyond availability: it means the concurrency
    guarantee is exercised across processes in production, not just across
    threads in one.
  EOT
  type        = number
  default     = 2
}

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

variable "core_table_name" {
  description = "Name of the core budget-state table."
  type        = string
  default     = "abc_core"
}

variable "ledger_table_name" {
  description = "Name of the immutable usage ledger table."
  type        = string
  default     = "abc_ledger"
}

variable "point_in_time_recovery" {
  description = <<-EOT
    Enable PITR on both tables.

    Strongly recommended: this is financial state, and the ledger is the record
    of record for what was actually spent.
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

variable "openai_secret_name" {
  description = "Secrets Manager secret holding the OpenAI API key. Empty to skip."
  type        = string
  default     = ""
}

variable "anthropic_secret_name" {
  description = "Secrets Manager secret holding the Anthropic API key. Empty to skip."
  type        = string
  default     = ""
}

variable "admin_key_secret_name" {
  description = "Secrets Manager secret holding the bootstrap admin API key."
  type        = string
  default     = ""
}

variable "enable_bedrock" {
  description = <<-EOT
    Grant the task role permission to invoke Bedrock models.

    Bedrock is the only provider that needs no stored API key -- it
    authenticates with the task role itself, removing an entire category of
    secret handling.
  EOT
  type        = bool
  default     = false
}

variable "bedrock_model_arns" {
  description = "Model ARNs the task may invoke. Defaults to all foundation models in-region."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "langfuse_host" {
  description = "Langfuse endpoint. Empty disables the integration."
  type        = string
  default     = ""
}

variable "alarm_email" {
  description = "Address subscribed to budget and runaway alarms. Empty to skip."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags applied to every resource."
  type        = map(string)
  default     = {}
}
