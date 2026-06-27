# ── Networking — minimal VPC for Image Builder build instances ────────────
#
# Image Builder launches a temporary EC2 instance to build each AMI. That
# instance needs:
#   - A subnet with a route to the internet (to reach SSM, S3, Windows Update)
#   - A security group allowing outbound traffic (no inbound needed —
#     Image Builder talks to the instance via SSM, not SSH/RDP)
#
# This creates a small dedicated VPC so the build pipeline doesn't depend on
# or interfere with any existing networking you might add later.

resource "aws_vpc" "image_builder" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "image-builder-vpc"
  }
}

resource "aws_internet_gateway" "image_builder" {
  vpc_id = aws_vpc.image_builder.id

  tags = {
    Name = "image-builder-igw"
  }
}

# Public subnet — build instance gets a public IP and reaches the internet
# directly via the IGW. Simpler and cheaper than a NAT gateway for a
# short-lived build instance with no inbound exposure.
resource "aws_subnet" "image_builder" {
  vpc_id                  = aws_vpc.image_builder.id
  cidr_block               = "10.42.1.0/24"
  availability_zone        = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch  = true

  tags = {
    Name = "image-builder-subnet"
  }
}

resource "aws_route_table" "image_builder" {
  vpc_id = aws_vpc.image_builder.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.image_builder.id
  }

  tags = {
    Name = "image-builder-rt"
  }
}

resource "aws_route_table_association" "image_builder" {
  subnet_id      = aws_subnet.image_builder.id
  route_table_id = aws_route_table.image_builder.id
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ── Security group — outbound only ─────────────────────────────────────────
#
# No inbound rules: Image Builder uses SSM Session Manager to communicate
# with the build instance, not SSH/RDP, so port 22/3389 inbound is not needed.

resource "aws_security_group" "image_builder" {
  name        = "image-builder-sg"
  description = "Outbound-only SG for EC2 Image Builder build instances"
  vpc_id      = aws_vpc.image_builder.id

  egress {
    description = "Allow all outbound (SSM, S3, Windows Update, etc.)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "image-builder-sg"
  }
}
