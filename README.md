# auto-ec2-image-builder

Automated pipeline that builds patched Windows EC2 AMIs via EC2 Image Builder,
chaining each successful build as the parent image for the next run.

## Architecture

```
Terraform (terraform/)          →  owns static infra (rarely changes)
  - Components (from components/*.yml)
  - Initial Image Recipe (v1.0.1)
  - Infrastructure Configuration
  - Distribution Configuration
  - Image Pipeline
  - Seed SSM parameter (/latest_ami_id)

GitLab CI (.gitlab-ci.yml)      →  owns per-run mutation
  - Upload-Installers:
      Downloads Python, CloudWatch Agent MSI, and Python wheels
      from the internet and syncs them to S3
  - Trigger-Image-Pipeline (image_pipeline_orchestrator.py):
      1. Reads /latest_ami_id from SSM
      2. Creates a new recipe version with that AMI as parent
      3. Points the pipeline at the new recipe version
      4. Triggers the build, polls until complete
      5. Writes the new output AMI ID back to /latest_ami_id
  - Launch-Instance (launch_instance.py):
      1. Reads latest AMI ID from SSM
      2. Creates key pair (saves PEM to SSM), security group,
         and IAM instance profile if they don't exist
      3. Terminates any existing instance with the same name
      4. Launches a new instance and prints RDP connection details
```

Terraform and GitLab never fight over the same fields — `parent_image` on the
recipe and `image_recipe_arn` on the pipeline are both marked
`lifecycle { ignore_changes }` in Terraform, since GitLab manages them after
the initial `terraform apply`.

## Repo layout

```
.
├── .gitlab-ci.yml
├── requirements.txt
├── image_pipeline_orchestrator.py
├── launch_instance.py
├── terraform/
│   ├── main.tf
│   └── variables.tf
└── components/
    ├── install_cw_config_window.yml
    ├── install_window_package.yml
    └── update_wins_os.yml
```

## What gets baked into the AMI

| Component | What it does |
|---|---|
| `install_cw_config_window.yml` | Installs CloudWatch Agent MSI, writes agent config to `ProgramData`, sets service to auto-start on boot |
| `install_window_package.yml` | Installs Python 3.11.9, installs AWS CLI, downloads and installs Python wheels from S3 |
| `update_wins_os.yml` | Runs PSWindowsUpdate, applies all pending patches, reboots, verifies no pending reboots remain |

### Python libraries bundled (offline wheels)

`boto3`, `botocore`,`requests`, `urllib3`, 
`pandas`, `openpyxl`, `python-dotenv`, `pyyaml`, 
`loguru`, `tqdm`, `tabulate`

## One-time setup

### 1. Terraform backend
Edit the `backend "s3" {}` block in `terraform/main.tf` with your state
bucket, key, and region.

### 2. terraform.tfvars
Create `terraform/terraform.tfvars`:
```hcl
initial_parent_image_ami = "ami-xxxxxxxxxxxxxxxxx"  # seed Windows AMI
```

Find a current Windows Server 2025 AMI to seed with:
```bash
aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=Windows_Server-2025-English-Full-Base-*" \
  --query 'Images | sort_by(@, &CreationDate)[-1].ImageId' \
  --output text --region us-east-1
```

### 3. Run Terraform once
```bash
cd terraform
terraform init
terraform plan
terraform apply
```

This provisions the 3 components, initial recipe (v1.0.1), infra config,
dist config, pipeline, and seeds the `/latest_ami_id` SSM parameter.

After `apply`, run `terraform output` to get the exact resource names
needed for the GitLab CI/CD variables below.

### 4. GitLab CI/CD Variables
Settings → CI/CD → Variables:

| Key | Notes |
|---|---|
| `AWS_ACCESS_KEY_ID` | protected + masked |
| `AWS_SECRET_ACCESS_KEY` | protected + masked |
| `AWS_ACCOUNT_ID` | |

### 5. IAM permissions
The access key needs:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "imagebuilder:ListImagePipelines",
        "imagebuilder:ListInfrastructureConfigurations",
        "imagebuilder:ListDistributionConfigurations",
        "imagebuilder:ListComponents",
        "imagebuilder:ListComponentBuildVersions",
        "imagebuilder:CreateImageRecipe",
        "imagebuilder:DeleteImageRecipe",
        "imagebuilder:UpdateImagePipeline",
        "imagebuilder:StartImagePipelineExecution",
        "imagebuilder:DeleteImagePipeline",
        "imagebuilder:DeleteComponent",
        "imagebuilder:GetImage",
        "ssm:GetParameter",
        "ssm:PutParameter",
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:CreateKeyPair",
        "ec2:DescribeKeyPairs",
        "ec2:CreateSecurityGroup",
        "ec2:DescribeSecurityGroups",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:CreateTags",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:CreateInstanceProfile",
        "iam:AddRoleToInstanceProfile",
        "iam:GetInstanceProfile",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

## Running

### Image build
1. Run `Upload-Installers` — downloads Python, CW Agent MSI, and Python
   wheels and syncs them to S3. Only needed when installers change.
2. Run `Trigger-Image-Pipeline` — creates a new recipe version, triggers
   the build, polls until complete (can take 1-4+ hours due to Windows
   Update), and updates `/latest_ami_id` on success.
3. Run `Launch-Instance` (manual) — launches a fresh instance from the
   latest AMI with RDP access. Terminates any existing instance with the
   same name first.

### Terraform changes
Push to `terraform/` or `components/` → validate and plan run automatically;
apply requires a manual click.

### Destroy everything
Click `Terraform-Destroy` in the pipeline — deletes Image Builder pipelines,
recipes, and components in dependency order, then runs `terraform destroy`.

## Connecting to the instance

After `Launch-Instance` completes, the job log prints:
```
Instance ID : i-xxxxxxxxxxxxxxxxx
Public IP   : x.x.x.x
RDP to      : x.x.x.x:3389
Username    : Administrator
Password    : Decrypt via EC2 console using key pair 'tfpractice-opsvm-keypair'
            : (or retrieve PEM from SSM: /tfpractice/keypairs/tfpractice-opsvm-keypair)
```

To decrypt the password:
1. EC2 Console → Instances → select instance → Actions → Security → Get Windows password
2. Paste the PEM key retrieved from SSM parameter `/tfpractice/keypairs/tfpractice-opsvm-keypair`

## Verifying the AMI after launch

```powershell
# Python
python --version
pip list

# CloudWatch Agent
Get-Service AmazonCloudWatchAgent
Get-Content "C:\ProgramData\Amazon\AmazonCloudWatchAgent\amazon-cloudwatch-agent.json"

# SSM Agent
Get-Service AmazonSSMAgent

# Windows Updates
Import-Module PSWindowsUpdate
Get-WURebootStatus  # RebootRequired should be False

# AWS CLI
aws --version
```

## Notes

- **Progressive patching** — each build uses the previous successful AMI as
  its parent, so Windows patches accumulate over time. Components are
  idempotent: they skip installation if the software is already present and
  only reapply what is missing.
- **Recipe versions accumulate** — the AWS Console shows one row per recipe
  name; click in to see the full version history. No auto-cleanup.
- **Failed builds** — the orchestrator does not update `/latest_ami_id` on
  failure, so the next run retries from the last known-good AMI.
- **Component encoding** — all component YAML files must be plain ASCII.
  Em dashes, smart quotes, or any non-ASCII characters in PowerShell blocks
  will cause parse errors in the Image Builder build environment.
- **Key pair PEM** — stored in SSM at `/tfpractice/keypairs/tfpractice-opsvm-keypair`
  as a SecureString. Retrieve with:
  ```bash
  aws ssm get-parameter \
    --name /tfpractice/keypairs/tfpractice-opsvm-keypair \
    --with-decryption \
    --query Parameter.Value \
    --output text > opsvm.pem
  ```