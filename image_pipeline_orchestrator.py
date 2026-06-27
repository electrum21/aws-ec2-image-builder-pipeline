#!/usr/bin/env python3
"""
Image Builder orchestration script.

Responsibilities:
  1. Read the latest AMI ID from SSM Parameter Store
  2. Create a new image recipe version with that AMI as the parent
  3. Update the image pipeline to point at the new recipe version
  4. Trigger the pipeline execution
  5. Poll until the build completes
  6. Write the new output AMI ID back to SSM for the next run

All ARNs (pipeline, infra config, dist config, components) are resolved
automatically via boto3 list_* calls, using the resource *names* as the
stable lookup key. This means GitLab only needs to know names, not ARNs —
ARNs never need to be manually copy-pasted from `terraform output` after
every apply.

Environment variables required:
  AWS_REGION                      e.g. us-east-1
  AWS_ACCOUNT_ID                  12-digit account ID
  RECIPE_NAME                     e.g. tfpractice-windows-recipe
  IMAGE_PIPELINE_NAME             name of the existing Image Builder pipeline
  INFRA_CONFIG_NAME                name of the infrastructure configuration
  DIST_CONFIG_NAME                 name of the distribution configuration
  CW_COMPONENT_NAME                name of the CloudWatch agent component
  PKG_COMPONENT_NAME               name of the software package component
  WU_COMPONENT_NAME                name of the Windows Update component
  SSM_PARAMETER_NAME              default: /latest_ami_id
  POLL_INTERVAL_SECONDS            default: 60
  BUILD_TIMEOUT_SECONDS             default: 14400 (4 hours)
"""

import os
import sys
import time
import logging
import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────

def get_env(name: str, default: str = None, required: bool = True) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        log.error(f"Missing required environment variable: {name}")
        sys.exit(1)
    return value


AWS_REGION = get_env("AWS_REGION", default="us-east-1", required=False)
AWS_ACCOUNT_ID = get_env("AWS_ACCOUNT_ID", default="357229909357", required=False)
RECIPE_NAME = get_env("RECIPE_NAME", default="tfpractice-windows-recipe", required=False)
IMAGE_PIPELINE_NAME = get_env("IMAGE_PIPELINE_NAME", default="tfpractice-windows-pipeline", required=False)
INFRA_CONFIG_NAME = get_env("INFRA_CONFIG_NAME", default="tfpractice-windows-infra-config", required=False)
DIST_CONFIG_NAME = get_env("DIST_CONFIG_NAME", default="tfpractice-windows-dist-config", required=False)
CW_COMPONENT_NAME = get_env("CW_COMPONENT_NAME", default="install-cw-config-window", required=False)
PKG_COMPONENT_NAME = get_env("PKG_COMPONENT_NAME", default="install-window-package", required=False)
WU_COMPONENT_NAME = get_env("WU_COMPONENT_NAME", default="update-wins-os", required=False)
SSM_PARAMETER_NAME = get_env("SSM_PARAMETER_NAME", default="/latest_ami_id", required=False)
POLL_INTERVAL_SECONDS = int(get_env("POLL_INTERVAL_SECONDS", default="60", required=False))
BUILD_TIMEOUT_SECONDS = int(get_env("BUILD_TIMEOUT_SECONDS", default="14400", required=False))

ssm_client = boto3.client("ssm", region_name=AWS_REGION)
imagebuilder_client = boto3.client("imagebuilder", region_name=AWS_REGION)
logs_client = boto3.client("logs", region_name=AWS_REGION)


# ── ARN resolution (so GitLab only needs to know names, not ARNs) ─────────

def get_pipeline_arn(name: str) -> str:
    log.info(f"Resolving Image Pipeline ARN for name: {name}")
    paginator = imagebuilder_client.get_paginator("list_image_pipelines")
    for page in paginator.paginate():
        for pipeline in page.get("imagePipelineList", []):
            if pipeline["name"] == name:
                log.info(f"Found pipeline ARN: {pipeline['arn']}")
                return pipeline["arn"]
    log.error(f"No Image Pipeline found with name: {name}")
    sys.exit(1)


def get_infra_config_arn(name: str) -> str:
    log.info(f"Resolving Infrastructure Configuration ARN for name: {name}")
    paginator = imagebuilder_client.get_paginator("list_infrastructure_configurations")
    for page in paginator.paginate():
        for cfg in page.get("infrastructureConfigurationSummaryList", []):
            if cfg["name"] == name:
                log.info(f"Found infra config ARN: {cfg['arn']}")
                return cfg["arn"]
    log.error(f"No Infrastructure Configuration found with name: {name}")
    sys.exit(1)


def get_dist_config_arn(name: str) -> str:
    log.info(f"Resolving Distribution Configuration ARN for name: {name}")
    paginator = imagebuilder_client.get_paginator("list_distribution_configurations")
    for page in paginator.paginate():
        for cfg in page.get("distributionConfigurationSummaryList", []):
            if cfg["name"] == name:
                log.info(f"Found dist config ARN: {cfg['arn']}")
                return cfg["arn"]
    log.error(f"No Distribution Configuration found with name: {name}")
    sys.exit(1)


def get_component_arn(name: str) -> str:
    """
    Find the latest version ARN of a component owned by this account.
    Components are versioned like recipes, so we take the highest semantic version.
    """
    log.info(f"Resolving Component ARN for name: {name}")
    paginator = imagebuilder_client.get_paginator("list_components")
    matches = []
    for page in paginator.paginate(owner="Self"):
        for component in page.get("componentVersionList", []):
            if component["name"] == name:
                matches.append(component)

    if not matches:
        log.error(f"No Component found with name: {name}")
        sys.exit(1)

    # componentVersionList entries include a "version" semantic string; pick the latest
    latest = sorted(matches, key=lambda c: [int(p) for p in c["version"].split(".")])[-1]
    log.info(f"Found component ARN: {latest['arn']} (version {latest['version']})")
    return latest["arn"]


def get_latest_recipe_version(name: str) -> str:
    """
    Find the highest existing semantic version for a recipe name.
    Returns "0.0.0" if no versions exist yet (first-ever run).
    """
    log.info(f"Resolving latest recipe version for name: {name}")
    paginator = imagebuilder_client.get_paginator("list_image_recipes")
    versions = []
    for page in paginator.paginate(owner="Self"):
        for recipe in page.get("imageRecipeSummaryList", []):
            if recipe["name"] == name:
                # The summary list does not include a "version" field.
                # The version is the last segment of the ARN:
                # arn:aws:imagebuilder:<region>:<account>:image-recipe/<name>/<version>
                version = recipe["arn"].split("/")[-1]
                versions.append(version)

    if not versions:
        log.info(f"No existing versions found for recipe {name} — starting from 0.0.0")
        return "0.0.0"

    latest = sorted(versions, key=lambda v: [int(p) for p in v.split(".")])[-1]
    log.info(f"Latest existing recipe version: {latest}")
    return latest


def bump_patch_version(version: str) -> str:
    """Increment the patch (third) segment of a semantic version string."""
    major, minor, patch = (int(p) for p in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


# ── Steps ─────────────────────────────────────────────────────────────────

def get_latest_ami() -> str:
    """Read the latest AMI ID from SSM Parameter Store."""
    log.info(f"Reading SSM parameter: {SSM_PARAMETER_NAME}")
    try:
        response = ssm_client.get_parameter(Name=SSM_PARAMETER_NAME)
        ami_id = response["Parameter"]["Value"]
        log.info(f"Current base AMI: {ami_id}")
        return ami_id
    except ClientError as e:
        log.error(f"Failed to read SSM parameter: {e}")
        sys.exit(1)


def create_new_recipe_version(parent_ami: str, component_arns: list[str]) -> tuple[str, str]:
    """Create a new image recipe version with the given parent AMI.

    The new version number is computed by querying AWS for the current
    highest existing version and incrementing the patch segment — this is
    more robust than deriving it from a GitLab counter (which increments on
    every pipeline run, not just successful recipe creations, and can drift
    from what actually exists in AWS).
    """
    current_version = get_latest_recipe_version(RECIPE_NAME)
    new_version = bump_patch_version(current_version)
    log.info(f"Creating recipe version {new_version} (previous: {current_version}) with parent image {parent_ami}")

    try:
        imagebuilder_client.create_image_recipe(
            name=RECIPE_NAME,
            semanticVersion=new_version,
            parentImage=parent_ami,
            components=[{"componentArn": arn} for arn in component_arns],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidParameterValueException" and "already exists" in str(e):
            log.warning(f"Recipe version {new_version} already exists, reusing it")
        else:
            log.error(f"Failed to create recipe version: {e}")
            sys.exit(1)

    new_recipe_arn = (
        f"arn:aws:imagebuilder:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
        f"image-recipe/{RECIPE_NAME}/{new_version}"
    )
    log.info(f"New recipe ARN: {new_recipe_arn}")
    return new_recipe_arn, new_version


def update_pipeline(recipe_arn: str, pipeline_arn: str, infra_config_arn: str, dist_config_arn: str) -> None:
    """Point the image pipeline at the new recipe version."""
    log.info(f"Updating pipeline to use recipe: {recipe_arn}")
    try:
        imagebuilder_client.update_image_pipeline(
            imagePipelineArn=pipeline_arn,
            imageRecipeArn=recipe_arn,
            infrastructureConfigurationArn=infra_config_arn,
            distributionConfigurationArn=dist_config_arn,
        )
        log.info("Pipeline updated successfully")
    except ClientError as e:
        log.error(f"Failed to update pipeline: {e}")
        sys.exit(1)


def trigger_build(pipeline_arn: str) -> str:
    """Start the pipeline execution and return the image build version ARN."""
    log.info("Triggering pipeline execution")
    try:
        response = imagebuilder_client.start_image_pipeline_execution(
            imagePipelineArn=pipeline_arn
        )
        build_arn = response["imageBuildVersionArn"]
        log.info(f"Build started: {build_arn}")
        return build_arn
    except ClientError as e:
        log.error(f"Failed to start pipeline execution: {e}")
        sys.exit(1)


def _derive_log_group(build_arn: str) -> str:
    """
    Image Builder writes workflow logs to:
      /aws/imagebuilder/<recipe-name>
    The build ARN has the form:
      arn:aws:imagebuilder:<region>:<account>:image/<recipe-name>/<version>/<build-id>
    """
    # segment index: 0=arn 1=aws 2=imagebuilder 3=region 4=account 5=image/<name>/...
    resource = build_arn.split(":", 5)[-1]          # image/<name>/<version>/<build-id>
    recipe_name = resource.split("/")[1]
    return f"/aws/imagebuilder/{recipe_name}"


def _stream_label(stream_name: str) -> str:
    """
    Derive a short human-readable label from a CW stream name.
    Image Builder stream names look like:
      <build-id>/<instance-id>/ssm/<command-id>/stdout
      <build-id>/<instance-id>/ssm/<command-id>/stderr
      <build-id>/workflow  (or similar)
    """
    parts = stream_name.rstrip("/").split("/")
    if parts[-1] in ("stdout", "stderr") and len(parts) >= 2:
        short_cmd = parts[-2][:8]
        return f"ssm/{short_cmd}/{parts[-1]}"
    return parts[-1] or stream_name


def _stream_cloudwatch_logs(log_group: str, since_ms: int) -> int:
    """
    Print any new CloudWatch log events from *all* streams in *log_group*
    that arrived after *since_ms* (epoch milliseconds).

    Paginates through every stream so SSM stdout/stderr streams are never
    missed. The old limit=5 approach only fetched the 5 most recently active
    streams, which tend to be workflow orchestration streams rather than the
    component execution streams where actual errors appear.

    Returns the timestamp of the latest event seen, or *since_ms* if nothing
    arrived yet.
    """
    latest_ts = since_ms

    all_streams = []
    paginator = logs_client.get_paginator("describe_log_streams")
    try:
        for page in paginator.paginate(logGroupName=log_group):
            all_streams.extend(page.get("logStreams", []))
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "AccessDeniedException"):
            log.debug(f"CW log group not available yet ({log_group}): {e}")
        else:
            log.warning(f"Failed to list CW log streams for {log_group}: {e}")
        return latest_ts

    for stream in all_streams:
        stream_name = stream["logStreamName"]
        label = _stream_label(stream_name)
        try:
            events_resp = logs_client.get_log_events(
                logGroupName=log_group,
                logStreamName=stream_name,
                startTime=since_ms + 1,   # exclusive lower bound
                startFromHead=True,
            )
        except ClientError as e:
            log.warning(f"Failed to read CW stream {stream_name}: {e}")
            continue

        for event in events_resp.get("events", []):
            ts_ms = event["timestamp"]
            message = event["message"].rstrip()
            ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms / 1000))
            log.info(f"[CW:{label}] {ts_str}Z  {message}")
            if ts_ms > latest_ts:
                latest_ts = ts_ms

    return latest_ts


def wait_for_build(build_arn: str) -> str:
    """Poll the build status until it completes, streaming CloudWatch logs inline."""
    log.info(f"Polling build status every {POLL_INTERVAL_SECONDS}s (timeout: {BUILD_TIMEOUT_SECONDS}s)")
    log.info(f"Build ARN: {build_arn}")

    log_group = _derive_log_group(build_arn)
    log.info(f"Tailing CloudWatch log group: {log_group}")

    elapsed = 0
    terminal_states = {"AVAILABLE", "FAILED", "CANCELLED", "DEPRECATED"}
    # Track the latest CW event timestamp so we never re-print the same line.
    cw_cursor_ms = int(time.time() * 1000)
    prev_status = None

    while elapsed < BUILD_TIMEOUT_SECONDS:
        # ── 1. Check Image Builder build status ──────────────────────────
        try:
            response = imagebuilder_client.get_image(imageBuildVersionArn=build_arn)
            image = response["image"]
            status = image["state"]["status"]
            reason = image["state"].get("reason", "")

            if status != prev_status:
                log.info(f"Build status changed: {prev_status} → {status}" + (f" ({reason})" if reason else ""))
                prev_status = status
            else:
                log.info(f"Build status: {status} (elapsed: {elapsed}s)")

            # ── 2. Stream any new CW log lines ───────────────────────────
            cw_cursor_ms = _stream_cloudwatch_logs(log_group, cw_cursor_ms)

            if status in terminal_states:
                # One final CW drain before returning
                cw_cursor_ms = _stream_cloudwatch_logs(log_group, cw_cursor_ms)
                if status != "AVAILABLE":
                    log.error(f"Build failed — final state: {status}" + (f": {reason}" if reason else ""))
                return status

        except ClientError as e:
            log.warning(f"Error checking build status (will retry): {e}")

        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    log.error(f"Build did not complete within {BUILD_TIMEOUT_SECONDS}s timeout")
    return "TIMEOUT"


def get_output_ami(build_arn: str) -> str:
    """Fetch the output AMI ID from a completed build."""
    try:
        response = imagebuilder_client.get_image(imageBuildVersionArn=build_arn)
        amis = response["image"]["outputResources"]["amis"]
        if not amis:
            log.error("No output AMIs found on completed build")
            sys.exit(1)
        ami_id = amis[0]["image"]
        log.info(f"New AMI built: {ami_id}")
        return ami_id
    except (ClientError, KeyError, IndexError) as e:
        log.error(f"Failed to retrieve output AMI: {e}")
        sys.exit(1)


def update_ssm_parameter(ami_id: str) -> None:
    """Write the new AMI ID back to SSM for the next pipeline run."""
    log.info(f"Updating SSM parameter {SSM_PARAMETER_NAME} -> {ami_id}")
    try:
        ssm_client.put_parameter(
            Name=SSM_PARAMETER_NAME,
            Value=ami_id,
            Type="String",
            Overwrite=True,
        )
        log.info("SSM parameter updated successfully")
    except ClientError as e:
        log.error(f"Failed to update SSM parameter: {e}")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    log.info("=== Starting Image Builder orchestration ===")

    # Resolve all ARNs from their stable names — nothing hardcoded or
    # injected from terraform output; always fetched live from AWS.
    pipeline_arn = get_pipeline_arn(IMAGE_PIPELINE_NAME)
    infra_config_arn = get_infra_config_arn(INFRA_CONFIG_NAME)
    dist_config_arn = get_dist_config_arn(DIST_CONFIG_NAME)
    component_arns = [
        get_component_arn(CW_COMPONENT_NAME),
        get_component_arn(PKG_COMPONENT_NAME),
        get_component_arn(WU_COMPONENT_NAME),
    ]

    parent_ami = get_latest_ami()
    recipe_arn, version = create_new_recipe_version(parent_ami, component_arns)
    update_pipeline(recipe_arn, pipeline_arn, infra_config_arn, dist_config_arn)
    build_arn = trigger_build(pipeline_arn)

    status = wait_for_build(build_arn)

    if status != "AVAILABLE":
        log.error(f"Build finished with non-success status: {status}")
        log.error("SSM parameter will NOT be updated — keeping previous AMI as latest")
        sys.exit(1)

    new_ami = get_output_ami(build_arn)
    update_ssm_parameter(new_ami)

    log.info("=== Image Builder orchestration complete ===")
    log.info(f"Recipe version: {version}")
    log.info(f"New AMI: {new_ami}")


if __name__ == "__main__":
    main()