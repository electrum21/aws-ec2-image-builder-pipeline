variable "aws_region" {
  description = "AWS region to deploy Image Builder resources into"
  type        = string
  default     = "us-east-1"
}

variable "recipe_name" {
  description = "Name of the Image Builder recipe (must match RECIPE_NAME in GitLab CI variables)"
  type        = string
  default     = "tfpractice-windows-recipe"
}

variable "image_pipeline_name" {
  description = "Name of the Image Builder pipeline (must match IMAGE_PIPELINE_NAME in GitLab CI variables)"
  type        = string
  default     = "tfpractice-windows-pipeline"
}

variable "infra_config_name" {
  description = "Name of the infrastructure configuration (must match INFRA_CONFIG_NAME in GitLab CI variables)"
  type        = string
  default     = "tfpractice-windows-infra-config"
}

variable "dist_config_name" {
  description = "Name of the distribution configuration (must match DIST_CONFIG_NAME in GitLab CI variables)"
  type        = string
  default     = "tfpractice-windows-dist-config"
}

variable "installer_bucket" {
  description = "S3 bucket containing the installer files (uploaded by GitLab's upload_installers job)"
  type        = string
  default     = "ec2-image-builder-software-bucket"
}

variable "initial_parent_image_ami" {
  description = "Seed AMI ID used for recipe v1.0.0 and the initial /latest_ami_id SSM value. After this, GitLab manages updates."
  type        = string
}

# Note: instance_profile_name, subnet_id, and security_group_ids are no
# longer input variables — they're created by networking.tf and iam.tf
# and referenced directly as resource attributes in main.tf.
