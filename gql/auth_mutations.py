# gql/auth_mutations.py

import strawberry

from backends.local_auth import DEFAULT_USER_EMAIL, _lookup_account_id
from backends.wire import auth_backend

from .types import AuthPayload, LoginInput, UserType


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def login(self, input: LoginInput) -> AuthPayload:
        """
        Authenticate using the configured auth backend.
        For the OSS version, validates against RENTMATE_PASSWORD env var.
        """
        try:
            token = await auth_backend.login(password=input.password)
        except ValueError as e:
            raise ValueError("Invalid password") from e

        return AuthPayload(
            token=token,
            user=UserType(
                uid=str(_lookup_account_id()),
                username=DEFAULT_USER_EMAIL,
            ),
        )
