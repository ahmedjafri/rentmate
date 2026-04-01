import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Wrench, Loader2 } from 'lucide-react';
import { setVendorToken } from '@/lib/vendorAuth';

interface InviteInfo {
  name: string;
  company?: string;
  vendor_type?: string;
  invite_status: string;
  access_token?: string;
}

const VendorInvite = () => {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    fetch(`/api/vendor-invite/${token}`)
      .then((res) => {
        if (!res.ok) throw new Error('not found');
        return res.json();
      })
      .then((data: InviteInfo) => {
        setInfo(data);
        // Returning vendor — auto-auth and redirect
        if (data.access_token) {
          setVendorToken(data.access_token);
          navigate('/vendor-portal');
        }
      })
      .catch(() => setNotFound(true));
  }, [token, navigate]);

  const handleAccept = async () => {
    setAccepting(true);
    setError(null);
    try {
      const res = await fetch(`/api/vendor-invite/${token}/accept`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to accept invite');
      setVendorToken(data.access_token);
      navigate('/vendor-portal');
    } catch (e) {
      setError((e as Error).message);
      setAccepting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-md p-8 space-y-6">
        <div className="flex justify-center">
          <div className="bg-primary/10 rounded-full p-4">
            <Wrench className="h-8 w-8 text-primary" />
          </div>
        </div>

        <div className="text-center">
          <h1 className="text-2xl font-bold">RentMate Vendor Invite</h1>
        </div>

        {notFound && (
          <p className="text-center text-muted-foreground">
            This invite link is invalid or has expired. Please contact your property manager.
          </p>
        )}

        {!notFound && !info && (
          <div className="flex justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {info && !info.access_token && (
          <div className="space-y-4">
            <div className="text-center space-y-1">
              <p className="font-semibold text-lg">{info.name}</p>
              {info.company && <p className="text-muted-foreground text-sm">{info.company}</p>}
              {info.vendor_type && (
                <Badge variant="secondary" className="text-xs">{info.vendor_type}</Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground text-center">
              You've been invited to join RentMate as a vendor. Accept to view and respond to work requests.
            </p>

            {error && <p className="text-sm text-destructive text-center">{error}</p>}

            <Button onClick={handleAccept} className="w-full" disabled={accepting}>
              {accepting ? 'Accepting...' : 'Accept Invite'}
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
};

export default VendorInvite;
