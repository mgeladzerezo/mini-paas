variable "name" {
  description = "Name of the platform; used for the cluster and as a prefix for other resources."
  type        = string
  default     = "mini-paas"
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}

variable "cluster_admin_cidrs" {
  description = "CIDR blocks allowed to reach the public Kubernetes API endpoint."
  type        = list(string)
}

variable "dns_zone_name" {
  description = "Existing public Route 53 zone that holds the platform and app hostnames."
  type        = string
}

variable "general_node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "build_node_instance_types" {
  type    = list(string)
  default = ["c6i.xlarge"]
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "github_repository" {
  description = "owner/repo allowed to push platform images to ECR from GitHub Actions."
  type        = string
}
