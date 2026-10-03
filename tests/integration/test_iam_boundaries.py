import boto3
import pytest

from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


def _decision(action: str, resource: str) -> str:
    iam = boto3.client("iam")
    result = iam.simulate_principal_policy(
        PolicySourceArn=require_env("CIP_PIPELINE_ROLE_ARN"),
        ActionNames=[action],
        ResourceArns=[resource],
    )
    decision: str = result["EvaluationResults"][0]["EvalDecision"]
    return decision


@pytest.mark.parametrize(
    "action", ["dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"]
)
def test_pipeline_role_cannot_mutate_ledger(action: str) -> None:
    assert _decision(action, require_env("CIP_LEDGER_TABLE_ARN")) != "allowed"


def test_pipeline_role_cannot_read_secrets() -> None:
    assert _decision("secretsmanager:GetSecretValue", "*") != "allowed"


def test_pipeline_role_cannot_invoke_bedrock() -> None:
    assert _decision("bedrock:InvokeModel", "*") != "allowed"


def test_pipeline_role_can_append_to_ledger() -> None:
    assert _decision("dynamodb:PutItem", require_env("CIP_LEDGER_TABLE_ARN")) == "allowed"
