"""Verify the DynamoDB behaviours the engine depends on.

These are not tests of our code. They are tests of the assumptions our code
makes about the datastore, and they exist because every one of those assumptions
is load-bearing and silently version-fragile.

If any of these fail, the engine is unsafe on that backend even though its own
unit tests still pass -- which is exactly the failure mode worth catching early.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from abc_gateway.repo import attributes as A
from abc_gateway.repo.dynamo.serde import num, txt

pytestmark = pytest.mark.usefixtures("dynamo_repo")

CORE = "abc_core"


@pytest.fixture
def client(dynamo_repo):
    return dynamo_repo.client


def _seed(client, *, remaining: int) -> dict:
    key = {A.PK: txt("TNT#t#BUDGET#TEAM#x"), A.SK: txt("WINDOW#MONTH#2026-08")}
    client.put_item(
        TableName=CORE,
        Item={
            **key,
            A.REMAINING_NANO: num(remaining),
            A.LIMIT_NANO: num(remaining),
            A.COMMITTED_NANO: num(0),
            A.RESERVED_NANO: num(0),
        },
    )
    return key


class TestPreImageOnConditionFailure:
    """The behaviour the entire denial-message design rests on."""

    def test_cancellation_reasons_carry_the_item_pre_image(self, client) -> None:
        """Without this, a denial can only say "a condition failed".

        With it, the API can report the scope, the balance and the shortfall.
        This is the most version-fragile assumption in the system, so it is
        asserted directly rather than inferred from a passing race test.
        """
        key = _seed(client, remaining=10)

        with pytest.raises(ClientError) as exc:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": key,
                            "ConditionExpression": "#r >= :cost",
                            "UpdateExpression": "ADD #r :neg",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {
                                ":cost": num(999),
                                ":neg": num(-999),
                            },
                            "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
                        }
                    }
                ]
            )

        reasons = exc.value.response["CancellationReasons"]
        assert reasons[0]["Code"] == "ConditionalCheckFailed"
        assert "Item" in reasons[0], (
            "ReturnValuesOnConditionCheckFailure did not return the pre-image; "
            "precise budget denials are impossible on this backend"
        )
        assert reasons[0]["Item"][A.REMAINING_NANO]["N"] == "10"

    def test_reasons_are_positionally_aligned_with_actions(self, client) -> None:
        """Slot N's outcome must be reason N.

        The whole denial decoder is a positional lookup, so a backend that
        reordered or compacted these would silently attribute a failure to the
        wrong scope.
        """
        key = _seed(client, remaining=10)
        other = {A.PK: txt("TNT#t#BUDGET#TEAM#y"), A.SK: txt("WINDOW#MONTH#2026-08")}
        client.put_item(TableName=CORE, Item={**other, A.REMAINING_NANO: num(1000)})

        with pytest.raises(ClientError) as exc:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": other,
                            "ConditionExpression": "#r >= :ok",
                            "UpdateExpression": "ADD #r :zero",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":ok": num(1), ":zero": num(0)},
                        }
                    },
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": key,
                            "ConditionExpression": "#r >= :cost",
                            "UpdateExpression": "ADD #r :zero",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":cost": num(999), ":zero": num(0)},
                        }
                    },
                ]
            )

        reasons = exc.value.response["CancellationReasons"]
        assert len(reasons) == 2
        assert reasons[0]["Code"] in (None, "None")  # slot 0 passed
        assert reasons[1]["Code"] == "ConditionalCheckFailed"  # slot 1 failed


class TestLazyCreationArithmetic:
    def test_set_with_if_not_exists_and_subtraction_is_supported(self, client) -> None:
        """`SET x = if_not_exists(x, :limit) - :cost` creates and decrements atomically.

        `ADD` cannot do this -- on an absent attribute it yields `-cost` rather
        than `limit - cost` -- and you cannot SET and ADD the same path in one
        expression. This construct is the only way lazy window creation is
        race-free.
        """
        key = {A.PK: txt("TNT#t#BUDGET#TEAM#new"), A.SK: txt("WINDOW#MONTH#2026-09")}
        client.update_item(
            TableName=CORE,
            Key=key,
            ConditionExpression="attribute_not_exists(#pk) OR #r >= :cost",
            UpdateExpression="SET #r = if_not_exists(#r, :limit) - :cost",
            ExpressionAttributeNames={"#pk": A.PK, "#r": A.REMAINING_NANO},
            ExpressionAttributeValues={":limit": num(1000), ":cost": num(300)},
        )

        item = client.get_item(TableName=CORE, Key=key, ConsistentRead=True)["Item"]
        assert item[A.REMAINING_NANO]["N"] == "700"

    def test_the_or_branch_admits_a_second_concurrent_creator(self, client) -> None:
        """The clause that stops a window-boundary traffic spike self-inflicting.

        Both requests plan against "item absent". The second finds it present at
        commit and must fall through to the balance check rather than being
        rejected outright.
        """
        key = {A.PK: txt("TNT#t#BUDGET#TEAM#race"), A.SK: txt("WINDOW#MONTH#2026-09")}
        for _ in range(2):
            client.update_item(
                TableName=CORE,
                Key=key,
                ConditionExpression="attribute_not_exists(#pk) OR #r >= :cost",
                UpdateExpression="SET #r = if_not_exists(#r, :limit) - :cost",
                ExpressionAttributeNames={"#pk": A.PK, "#r": A.REMAINING_NANO},
                ExpressionAttributeValues={":limit": num(1000), ":cost": num(300)},
            )

        item = client.get_item(TableName=CORE, Key=key, ConsistentRead=True)["Item"]
        assert item[A.REMAINING_NANO]["N"] == "400"


class TestTransactionRules:
    def test_two_actions_on_one_item_are_rejected(self, client) -> None:
        """The rule that dictates the item layout.

        Agent status and agent budget counters live on separate items precisely
        so that authorization can check one and update the other in the same
        transaction.
        """
        key = _seed(client, remaining=100)
        with pytest.raises(ClientError) as exc:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": key,
                            "UpdateExpression": "ADD #r :one",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":one": num(1)},
                        }
                    },
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": key,
                            "UpdateExpression": "ADD #r :one",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":one": num(1)},
                        }
                    },
                ]
            )
        assert exc.value.response["Error"]["Code"] in (
            "ValidationException",
            "TransactionCanceledException",
        )

    def test_a_transaction_is_all_or_nothing(self, client) -> None:
        """One failed condition must leave every other item untouched.

        This is what makes "reserve from team AND agent AND session" a single
        decision rather than three that can partially apply.
        """
        good = {A.PK: txt("TNT#t#BUDGET#TEAM#good"), A.SK: txt("W")}
        client.put_item(TableName=CORE, Item={**good, A.REMAINING_NANO: num(1000)})
        bad = _seed(client, remaining=1)

        with pytest.raises(ClientError):
            client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": good,
                            "UpdateExpression": "SET #r = :new",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":new": num(500)},
                        }
                    },
                    {
                        "Update": {
                            "TableName": CORE,
                            "Key": bad,
                            "ConditionExpression": "#r >= :cost",
                            "UpdateExpression": "SET #r = :new",
                            "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                            "ExpressionAttributeValues": {":cost": num(999), ":new": num(0)},
                        }
                    },
                ]
            )

        item = client.get_item(TableName=CORE, Key=good, ConsistentRead=True)["Item"]
        assert item[A.REMAINING_NANO]["N"] == "1000", (
            "a partially applied transaction would let one scope be charged while another was not"
        )

    def test_cross_table_transactions_work(self, dynamo_repo, client) -> None:
        """Counters and the ledger entry must commit together or not at all.

        This is what makes splitting the ledger into its own table free: it buys
        IAM-enforced immutability without giving up atomicity.
        """
        key = _seed(client, remaining=1000)
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": CORE,
                        "Key": key,
                        "UpdateExpression": "ADD #r :neg",
                        "ExpressionAttributeNames": {"#r": A.REMAINING_NANO},
                        "ExpressionAttributeValues": {":neg": num(-100)},
                    }
                },
                {
                    "Put": {
                        "TableName": "abc_ledger",
                        "Item": {
                            A.PK: txt("TNT#t#LEDGER#a#2026-08"),
                            A.SK: txt("0001"),
                            "cost_nano": num(100),
                        },
                    }
                },
            ]
        )
        assert (
            client.get_item(
                TableName="abc_ledger",
                Key={A.PK: txt("TNT#t#LEDGER#a#2026-08"), A.SK: txt("0001")},
                ConsistentRead=True,
            )["Item"]["cost_nano"]["N"]
            == "100"
        )


class TestNumericFidelity:
    def test_large_nano_values_survive_a_round_trip(self, client) -> None:
        """$1,000,000 is 10^15 nano-USD.

        DynamoDB numbers carry 38 significant digits, so this is exact -- but a
        backend that routed numbers through a float would lose precision here,
        and the error would be invisible until it mattered.
        """
        key = {A.PK: txt("TNT#t#BUDGET#TEAM#big"), A.SK: txt("W")}
        huge = 1_000_000 * 10**9
        client.put_item(TableName=CORE, Item={**key, A.REMAINING_NANO: num(huge)})
        item = client.get_item(TableName=CORE, Key=key, ConsistentRead=True)["Item"]
        assert int(item[A.REMAINING_NANO]["N"]) == huge
