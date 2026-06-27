# ── Outputs — values you need for GitLab CI/CD variables ──────────────────

output "image_pipeline_name" {
  description = "Set as IMAGE_PIPELINE_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_image_pipeline.windows_pipeline.name
}

output "infra_config_name" {
  description = "Set as INFRA_CONFIG_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_infrastructure_configuration.this.name
}

output "dist_config_name" {
  description = "Set as DIST_CONFIG_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_distribution_configuration.this.name
}

output "cw_component_name" {
  description = "Set as CW_COMPONENT_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_component.cw_config.name
}

output "pkg_component_name" {
  description = "Set as PKG_COMPONENT_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_component.window_packages.name
}

output "wu_component_name" {
  description = "Set as WU_COMPONENT_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_component.update_wins_os.name
}

output "recipe_name" {
  description = "Set as RECIPE_NAME in GitLab CI/CD variables"
  value       = aws_imagebuilder_image_recipe.windows_recipe.name
}

# ── Outputs — networking/IAM, useful for verification/debugging ───────────

output "vpc_id" {
  description = "VPC created for Image Builder build instances"
  value       = aws_vpc.image_builder.id
}

output "subnet_id" {
  description = "Subnet the build instance launches into"
  value       = aws_subnet.image_builder.id
}

output "security_group_id" {
  description = "Security group attached to the build instance"
  value       = aws_security_group.image_builder.id
}

output "instance_profile_name" {
  description = "Instance profile attached to the build instance"
  value       = aws_iam_instance_profile.image_builder.name
}

output "instance_role_arn" {
  description = "IAM role ARN assumed by the build instance"
  value       = aws_iam_role.image_builder_instance.arn
}
