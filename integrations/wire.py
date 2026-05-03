from integrations.base import AuthBackend, SMSRouter, StorageBackend, VectorBackend
from integrations.litellm_vector import LiteLLMVectorBackend
from integrations.local_auth import LocalAuthBackend
from integrations.local_storage import LocalStorageBackend
from integrations.single_tenant_sms import SingleTenantSMSRouter

auth_backend: AuthBackend = LocalAuthBackend()
storage_backend: StorageBackend = LocalStorageBackend()
vector_backend: VectorBackend = LiteLLMVectorBackend()
sms_router: SMSRouter = SingleTenantSMSRouter()
