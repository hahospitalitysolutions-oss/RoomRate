from fastapi import APIRouter, Depends

from api.dependencies import AccountContext, get_account_context, get_accounts_repository
from api.repositories.accounts_repository import AccountsRepository
from api.schemas.auth import CurrentUserResponse

router = APIRouter()


@router.get("/me", response_model=CurrentUserResponse)
async def current_user(
    account: AccountContext = Depends(get_account_context),
    repository: AccountsRepository = Depends(get_accounts_repository),
) -> CurrentUserResponse:
    """Return current user/account state for Angular route guards."""
    overview = repository.get_account_overview(account.account_id)
    return CurrentUserResponse(
        account_id=account.account_id,
        auth_provider=account.auth_provider,
        auth_subject=account.auth_subject,
        email=account.email,
        **overview,
    )
