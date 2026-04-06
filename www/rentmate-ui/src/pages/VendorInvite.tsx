import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Loader2 } from 'lucide-react';
import { setVendorToken } from '@/lib/vendorAuth';
import { setTenantToken } from '@/lib/tenantAuth';

const PortalInvite = () => {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;

    // Try vendor token first, then tenant
    fetch(`/api/vendor-token/${token}`)
      .then((res) => {
        if (res.ok) return res.json().then((data) => {
          setVendorToken(data.access_token);
          navigate('/vendor-portal');
        });
        // Not a vendor token — try tenant
        return fetch(`/api/tenant-token/${token}`)
          .then((res2) => {
            if (!res2.ok) throw new Error('Invalid or expired link');
            return res2.json();
          })
          .then((data) => {
            setTenantToken(data.access_token);
            navigate('/tenant-portal');
          });
      })
      .catch((e) => setError((e as Error).message));
  }, [token, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-md p-8 space-y-6">
        <div className="text-center">
          {error ? (
            <p className="text-muted-foreground">
              This link is invalid or has expired. Please contact your property manager.
            </p>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading portal...</p>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
};

export default PortalInvite;
