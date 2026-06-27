# ── IAM — instance role/profile for the Image Builder build instance ──────
#
# The temporary EC2 instance that Image Builder launches needs permissions to:
#   - Be managed via SSM (required — this is how Image Builder controls the
#     instance and runs your component scripts; without this the build hangs
#     and eventually times out)
#   - Read installer files from your S3 bucket
#   - Write build logs to CloudWatch (the EC2InstanceProfileForImageBuilder
#     managed policy covers this plus a few other Image Builder essentials)

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "image_builder_instance" {
  name               = "ImageBuilderInstanceRole"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

# Required: lets Image Builder communicate with and control the build
# instance via SSM (no SSH/RDP needed).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.image_builder_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# AWS-managed policy with the baseline permissions Image Builder expects
# on the build instance (CloudWatch Logs, S3 read for the IB service
# bucket, etc).
resource "aws_iam_role_policy_attachment" "image_builder_core" {
  role       = aws_iam_role.image_builder_instance.name
  policy_arn = "arn:aws:iam::aws:policy/EC2InstanceProfileForImageBuilder"
}

# Custom policy: read access to your installer S3 bucket specifically,
# since the managed policies above don't know about your bucket.
resource "aws_iam_role_policy" "installer_bucket_read" {
  name = "InstallerBucketReadAccess"
  role = aws_iam_role.image_builder_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::${var.installer_bucket}",
          "arn:aws:s3:::${var.installer_bucket}/*",
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "image_builder" {
  name = "ImageBuilderInstanceProfile"
  role = aws_iam_role.image_builder_instance.name
}
