import boto3
import os
import sys
import json
import time

AWS_REGION = os.environ["AWS_REGION"]
SSM_PARAMETER_NAME = os.environ["SSM_PARAMETER_NAME"]
INSTANCE_TYPE = os.environ.get("INSTANCE_TYPE", "t3.micro")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "tfpractice-opsvm")
KEY_PAIR_NAME = os.environ.get("KEY_PAIR_NAME", "tfpractice-opsvm-keypair")
IAM_INSTANCE_PROFILE = os.environ.get("IAM_INSTANCE_PROFILE", "tfpractice-opsvm-profile")
VPC_NAME = os.environ.get("VPC_NAME", "")
SUBNET_ID = os.environ.get("SUBNET_ID", "")
ALLOWED_RDP_CIDR = os.environ.get("ALLOWED_RDP_CIDR", "0.0.0.0/0")

ec2 = boto3.client("ec2", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
iam = boto3.client("iam", region_name=AWS_REGION)


def get_latest_ami():
    print(f"Fetching latest AMI ID from SSM parameter {SSM_PARAMETER_NAME}...")
    resp = ssm.get_parameter(Name=SSM_PARAMETER_NAME)
    ami_id = resp["Parameter"]["Value"]
    print(f"Latest AMI: {ami_id}")
    return ami_id


def ensure_key_pair():
    print(f"Checking key pair '{KEY_PAIR_NAME}'...")
    try:
        ec2.describe_key_pairs(KeyNames=[KEY_PAIR_NAME])
        print(f"Key pair '{KEY_PAIR_NAME}' already exists.")
        return None
    except ec2.exceptions.ClientError:
        pass

    print(f"Creating key pair '{KEY_PAIR_NAME}'...")
    resp = ec2.create_key_pair(KeyName=KEY_PAIR_NAME, KeyType="rsa", KeyFormat="pem")
    key_material = resp["KeyMaterial"]
    # Save to SSM so it can be retrieved later without storing in GitLab artifacts
    ssm.put_parameter(
        Name=f"/tfpractice/keypairs/{KEY_PAIR_NAME}",
        Value=key_material,
        Type="SecureString",
        Overwrite=True,
    )
    print(f"Key pair created and saved to SSM at /tfpractice/keypairs/{KEY_PAIR_NAME}")
    return key_material


def ensure_security_group(vpc_id):
    sg_name = f"{INSTANCE_NAME}-sg"
    print(f"Checking security group '{sg_name}'...")

    resp = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [sg_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )
    if resp["SecurityGroups"]:
        sg_id = resp["SecurityGroups"][0]["GroupId"]
        print(f"Security group already exists: {sg_id}")
        return sg_id

    print(f"Creating security group '{sg_name}'...")
    resp = ec2.create_security_group(
        GroupName=sg_name,
        Description=f"Security group for {INSTANCE_NAME} OpsVM",
        VpcId=vpc_id,
    )
    sg_id = resp["GroupId"]

    # RDP inbound
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 3389,
                "ToPort": 3389,
                "IpRanges": [{"CidrIp": ALLOWED_RDP_CIDR, "Description": "RDP access"}],
            },
        ],
    )

    # HTTPS outbound for SSM + CW Agent (all outbound already default allowed,
    # but make it explicit)
    print(f"Security group created: {sg_id}")
    ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "Name", "Value": sg_name}])
    return sg_id


def ensure_iam_instance_profile():
    print(f"Checking IAM instance profile '{IAM_INSTANCE_PROFILE}'...")
    try:
        iam.get_instance_profile(InstanceProfileName=IAM_INSTANCE_PROFILE)
        print(f"Instance profile '{IAM_INSTANCE_PROFILE}' already exists.")
        return IAM_INSTANCE_PROFILE
    except iam.exceptions.NoSuchEntityException:
        pass

    role_name = f"{IAM_INSTANCE_PROFILE}-role"
    print(f"Creating IAM role '{role_name}'...")

    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    })

    iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust_policy)

    for policy in [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    ]:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        print(f"Attached {policy}")

    iam.create_instance_profile(InstanceProfileName=IAM_INSTANCE_PROFILE)
    iam.add_role_to_instance_profile(
        InstanceProfileName=IAM_INSTANCE_PROFILE,
        RoleName=role_name,
    )
    # IAM propagation delay
    print("Waiting for IAM profile to propagate...")
    time.sleep(15)
    print(f"Instance profile '{IAM_INSTANCE_PROFILE}' created.")
    return IAM_INSTANCE_PROFILE


def get_vpc_and_subnet():
    if SUBNET_ID:
        resp = ec2.describe_subnets(SubnetIds=[SUBNET_ID])
        vpc_id = resp["Subnets"][0]["VpcId"]
        return vpc_id, SUBNET_ID

    # Fall back to default VPC
    resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = resp["Vpcs"][0]["VpcId"]

    resp = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "map-public-ip-on-launch", "Values": ["true"]},
        ]
    )
    subnet_id = resp["Subnets"][0]["SubnetId"]
    print(f"Using default VPC {vpc_id}, subnet {subnet_id}")
    return vpc_id, subnet_id


def terminate_existing_instance():
    print(f"Checking for existing instance named '{INSTANCE_NAME}'...")
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["running", "stopped", "pending"]},
        ]
    )
    for r in resp["Reservations"]:
        for i in r["Instances"]:
            iid = i["InstanceId"]
            print(f"Terminating existing instance {iid}...")
            ec2.terminate_instances(InstanceIds=[iid])
            waiter = ec2.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[iid])
            print(f"Instance {iid} terminated.")


def launch_instance(ami_id, sg_id, subnet_id, profile_name):
    print(f"Launching instance from AMI {ami_id}...")
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        KeyName=KEY_PAIR_NAME,
        MinCount=1,
        MaxCount=1,
        NetworkInterfaces=[{
            "DeviceIndex": 0,
            "SubnetId": subnet_id,
            "Groups": [sg_id],
            "AssociatePublicIpAddress": True,
        }],
        IamInstanceProfile={"Name": profile_name},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": INSTANCE_NAME},
                {"Key": "ManagedBy", "Value": "gitlab-ci"},
                {"Key": "AMI", "Value": ami_id},
            ],
        }],
        MetadataOptions={
            "HttpTokens": "required",  # IMDSv2
            "HttpEndpoint": "enabled",
        },
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"Instance launched: {instance_id}")
    print("Waiting for instance to be running...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    # Get public IP
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    public_ip = resp["Reservations"][0]["Instances"][0].get("PublicIpAddress", "N/A")
    print(f"\n{'='*50}")
    print(f"Instance ready!")
    print(f"  Instance ID : {instance_id}")
    print(f"  Public IP   : {public_ip}")
    print(f"  RDP to      : {public_ip}:3389")
    print(f"  Username    : Administrator")
    print(f"  Password    : Decrypt via EC2 console using key pair '{KEY_PAIR_NAME}'")
    print(f"              : (or retrieve PEM from SSM: /tfpractice/keypairs/{KEY_PAIR_NAME})")
    print(f"{'='*50}\n")
    return instance_id, public_ip


def main():
    ami_id = get_latest_ami()
    vpc_id, subnet_id = get_vpc_and_subnet()
    ensure_key_pair()
    sg_id = ensure_security_group(vpc_id)
    profile_name = ensure_iam_instance_profile()
    terminate_existing_instance()
    launch_instance(ami_id, sg_id, subnet_id, profile_name)


if __name__ == "__main__":
    main()