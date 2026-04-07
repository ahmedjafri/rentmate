from backends.base import AuthBackend, SMSRouter, StorageBackend, VectorBackend
from backends.chroma_vector import ChromaVectorBackend
from backends.local_auth import LocalAuthBackend
from backends.local_storage import LocalStorageBackend
from backends.single_tenant_sms import SingleTenantSMSRouter

auth_backend: AuthBackend = LocalAuthBackend()
storage_backend: StorageBackend = LocalStorageBackend()
vector_backend: VectorBackend = ChromaVectorBackend()
sms_router: SMSRouter = SingleTenantSMSRouter()
