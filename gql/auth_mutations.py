# gql/auth_mutations.py

import strawberry

from integrations.wire import auth_backend

from .types import AuthPayload, LoginInput, UserType


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def login(self, input: LoginInput) -> AuthPayload:
        """
        Authenticate with email + password.
        Creates the account on first sign-up if no account with that email exists.
        """
        try:
            token, user = await auth_backend.login(
                password=input.password,
                email=input.email or None,
            )
        except ValueError as e:
            raise ValueError("Invalid password") from e

        return AuthPayload(
            token=token,
            user=UserType(
                uid=str(user.external_id),
                username=user.email or input.email or "",
            ),
        )
