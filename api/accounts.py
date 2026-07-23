from typing import Any

from fastapi import APIRouter, HTTPException

from backend_security import StrictRequestModel

import connected_accounts


router = APIRouter()

get_accounts = connected_accounts.get_accounts
add_account_fn = connected_accounts.add_account
remove_account_fn = connected_accounts.remove_account
sync_account_fn = connected_accounts.sync_account
PLATFORMS_LIST = connected_accounts.PLATFORMS


class AccountCreatePayload(StrictRequestModel):
    platform: str = ""
    label: str = ""
    credentials: Any | None = None


@router.get("/api/accounts")
def list_accounts():
    return {"accounts": get_accounts(), "platforms": PLATFORMS_LIST}


@router.post("/api/accounts")
def create_account(payload: AccountCreatePayload):
    platform = payload.platform
    label = payload.label
    credentials_ignored = payload.credentials is not None
    if platform not in PLATFORMS_LIST:
        raise HTTPException(400, f"Unknown platform: {platform}")
    acc_id = add_account_fn(platform, label)
    response = {"id": acc_id, "status": "connected"}
    if credentials_ignored:
        response["warning"] = "credentials_ignored"
    return response


@router.delete("/api/accounts/{account_id}")
def delete_account(account_id: str):
    remove_account_fn(account_id)
    return {"status": "removed"}


@router.post("/api/accounts/{account_id}/sync")
def sync_account(account_id: str):
    result = sync_account_fn(account_id)
    return result
