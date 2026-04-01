import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Wrench } from 'lucide-react';
import { setVendorToken } from '@/lib/vendorAuth';

interface InviteInfo {
  name: string;
  company?: string;
  vendor_type?: string;
  invite_status: string;
}

const schema = z
  .object({
    email: z.string().email('Valid email required'),
    password: z.string().min(8, 'Min 8 characters'),
    confirm: z.string(),
  })
  .refine((d) => d.password === d.confirm, {
    message: "Passwords don't match",
    path: ['confirm'],
  });

type FormValues = z.infer<typeof schema>;

const VendorInvite = () => {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (!token) return;
    fetch(`/api/vendor-invite/${token}`)
      .then((res) => {
        if (!res.ok) throw new Error('not found');
        return res.json();
      })
      .then((data) => setInfo(data))
      .catch(() => setNotFound(true));
  }, [token]);

  const onSubmit = async (values: FormValues) => {
    setServerError(null);
    try {
      const res = await fetch(`/api/vendor-invite/${token}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: values.email, password: values.password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Registration failed');
      setVendorToken(data.access_token);
      navigate('/vendor-portal');
    } catch (e) {
      setServerError((e as Error).message);
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
          <p className="text-center text-muted-foreground">Loading...</p>
        )}

        {info && info.invite_status === 'registered' && (
          <div className="space-y-4 text-center">
            <p className="font-semibold text-lg">{info.name}</p>
            {info.company && <p className="text-muted-foreground">{info.company}</p>}
            <p className="text-muted-foreground">You already have a RentMate account.</p>
            <Button asChild className="w-full">
              <Link to="/vendor-login">Log in →</Link>
            </Button>
          </div>
        )}

        {info && info.invite_status !== 'registered' && (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div className="text-center space-y-1">
              <p className="font-semibold text-lg">{info.name}</p>
              {info.company && <p className="text-muted-foreground text-sm">{info.company}</p>}
              {info.vendor_type && (
                <p className="text-sm text-muted-foreground">{info.vendor_type}</p>
              )}
            </div>
            <p className="text-sm text-muted-foreground text-center">
              Create your RentMate account to accept this invitation.
            </p>

            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" {...register('email')} placeholder="you@example.com" />
              {errors.email && (
                <p className="text-xs text-destructive">{errors.email.message}</p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" {...register('password')} />
              {errors.password && (
                <p className="text-xs text-destructive">{errors.password.message}</p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="confirm">Confirm Password</Label>
              <Input id="confirm" type="password" {...register('confirm')} />
              {errors.confirm && (
                <p className="text-xs text-destructive">{errors.confirm.message}</p>
              )}
            </div>

            {serverError && <p className="text-sm text-destructive">{serverError}</p>}

            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? 'Creating account...' : 'Create Account'}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
};

export default VendorInvite;
