# gql/auth_mutations.py

import strawberry
from backends.wire import auth_backend
from backends.local_auth import DEFAULT_USER_ID, DEFAULT_USER_EMAIL
from .types import LoginInput, AuthPayload, UserType


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
                uid=DEFAULT_USER_ID,
                username=DEFAULT_USER_EMAIL,
            ),
        )
