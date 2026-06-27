terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Fill in or pass via -backend-config; kept generic here intentionally.
    bucket = "tfpractice-terraform-state-dev"
    key    = "auto-ec2-image-builder-terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Components — built from the YAML files you already have ──────────────

resource "aws_imagebuilder_component" "cw_config" {
  name     = "install-cw-config-window"
  platform = "Windows"
  version  = "1.0.1"
  data     = templatefile("${path.module}/../components/install_cw_config_window.yml", {
    installerBucket = var.installer_bucket
  })

  lifecycle {
    create_before_destroy = true
  } 
}

resource "aws_imagebuilder_component" "window_packages" {
  name     = "install-window-package"
  platform = "Windows" 
  version  = "1.0.3" 
  data = templatefile("${path.module}/../components/install_window_package.yml", {
    installer_bucket = var.installer_bucket
    aws_region       = var.aws_region
  })  

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_imagebuilder_component" "update_wins_os" {
  name     = "update-wins-os"
  platform = "Windows"
  version  = "1.0.1"
  data     = file("${path.module}/../components/update_wins_os.yml")

  lifecycle {
    create_before_destroy = true
  }
}

# ── Initial recipe (version 1.0.0 — GitLab creates 1.0.x after this) ──────

resource "aws_imagebuilder_image_recipe" "windows_recipe" {
  name         = var.recipe_name
  version      = "1.0.1" 
  parent_image = var.initial_parent_image_ami

  component {
    component_arn = aws_imagebuilder_component.cw_config.arn
  }
  component {
    component_arn = aws_imagebuilder_component.window_packages.arn
  }
  component {
    component_arn = aws_imagebuilder_component.update_wins_os.arn
  }

  lifecycle {
    ignore_changes  = [parent_image]
  }
}

# ── Infrastructure configuration ───────────────────────────────────────────

resource "aws_imagebuilder_infrastructure_configuration" "this" {
  name                          = var.infra_config_name
  instance_profile_name         = aws_iam_instance_profile.image_builder.name
  instance_types                = ["t3.micro"]
  subnet_id                     = aws_subnet.image_builder.id
  security_group_ids            = [aws_security_group.image_builder.id]
  terminate_instance_on_failure = true
}

# ── Distribution configuration ───────────

resource "aws_imagebuilder_distribution_configuration" "this" {
  name = var.dist_config_name

  distribution {
    region = var.aws_region

    ami_distribution_configuration {
      name = "tfpractice-windows-{{ imagebuilder:buildDate }}"
    }
  }
}

# ── Image pipeline ───────────────────────────────────────────────────────

resource "aws_imagebuilder_image_pipeline" "windows_pipeline" {
  name                             = var.image_pipeline_name
  image_recipe_arn                 = aws_imagebuilder_image_recipe.windows_recipe.arn
  infrastructure_configuration_arn = aws_imagebuilder_infrastructure_configuration.this.arn
  distribution_configuration_arn   = aws_imagebuilder_distribution_configuration.this.arn

  lifecycle {
    ignore_changes = [image_recipe_arn]
  }
}

# ── Seed SSM parameter (only created once; GitLab updates the value) ──────

resource "aws_ssm_parameter" "latest_ami_id" {
  name  = "/latest_ami_id"
  type  = "String"
  value = var.initial_parent_image_ami

  lifecycle {
    ignore_changes = [value]
  }
}