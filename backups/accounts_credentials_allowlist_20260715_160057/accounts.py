from typing import Any, Dict

from fastapi import APIRouter, HTTPException

import connected_accounts


router = APIRouter()

get_accounts = connected_accounts.get_accounts
add_account_fn = connected_accounts.add_account
remove_account_fn = connected_accounts.remove_account
sync_account_fn = connected_accounts.sync_account
PLATFORMS_LIST = connected_accounts.PLATFORMS


@router.get("/api/accounts")
def list_accounts():
    return {"accounts": get_accounts(), "platforms": PLATFORMS_LIST}


@router.post("/api/accounts")
def create_account(payload: Dict[str, Any]):
    platform = payload.get("platform", "")
    label = payload.get("label", "")
    if platform not in PLATFORMS_LIST:
        raise HTTPException(400, f"Unknown platform: {platform}")
    acc_id = add_account_fn(platform, label)
    return {"id": acc_id, "status": "connected"}


@router.delete("/api/accounts/{account_id}")
def delete_account(account_id: str):
    remove_account_fn(account_id)
    return {"status": "removed"}


@router.post("/api/accounts/{account_id}/sync")
def sync_account(account_id: str):
    result = sync_account_fn(account_id)
    return result
