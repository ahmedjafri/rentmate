import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { authFetch } from '@/lib/auth';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  FileText, Upload, Search, Tag, CheckCircle2, Clock,
  Loader2, AlertCircle, Trash2, Bot, FileSpreadsheet, FileImage,
} from 'lucide-react';
import { PageLoader } from '@/components/ui/page-loader';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';
import {
  DocumentStatus, DocumentType, ManagedDocument,
  documentTypeLabels,
} from '@/data/mockData';
import { cn } from '@/lib/utils';

const statusConfig: Record<DocumentStatus, { label: string; icon: React.ElementType; className: string }> = {
  uploading: { label: 'Uploading', icon: Loader2, className: 'bg-muted text-muted-foreground' },
  analyzing: { label: 'Analyzing…', icon: Bot, className: 'bg-primary/15 text-primary' },
  ready: { label: 'Done', icon: CheckCircle2, className: 'bg-accent/15 text-accent' },
  error: { label: 'Error', icon: AlertCircle, className: 'bg-destructive/15 text-destructive' },
};

const fileIcon = (fileType: string) => {
  if (fileType.includes('image')) return FileImage;
  if (fileType.includes('spreadsheet') || fileType.includes('excel') || fileType.includes('csv')) return FileSpreadsheet;
  return FileText;
};

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

type FilterType = 'all' | DocumentType;

const backendStatusToFrontend = (s: string): DocumentStatus => {
  if (s === 'done') return 'ready';
  if (s === 'error') return 'error';
  return 'analyzing';
};

const Documents = () => {
  const navigate = useNavigate();
  const { documents, addDocument, replaceDocument, removeDocument, updateDocument } = useApp();
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<FilterType>('all');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Initial load
  useEffect(() => {
    authFetch('/api/documents')
      .then(r => r.ok ? r.json() : [])
      .then((items: Array<{ id: string; filename: string; document_type: string; status: string; created_at: string }>) => {
        items.forEach(item => {
          addDocument({
            id: item.id,
            fileName: item.filename,
            fileType: 'application/octet-stream',
            fileSize: 0,
            documentType: (item.document_type as DocumentType) || 'other',
            status: backendStatusToFrontend(item.status),
            uploadedAt: new Date(item.created_at),
            tags: [],
          });
        });
      })
      .catch(() => {})
      .finally(() => setIsLoading(false));
  }, []);

  // Poll analyzing documents until they finish processing
  useEffect(() => {
    const analyzing = documents.filter(d => d.status === 'analyzing');
    if (analyzing.length === 0) return;
    const interval = setInterval(async () => {
      for (const doc of analyzing) {
        const res = await authFetch(`/api/document/${doc.id}`).catch(() => null);
        if (!res || !res.ok) continue;
        const data = await res.json();
        if (data.status !== 'pending' && data.status !== 'processing') {
          updateDocument(doc.id, {
            status: backendStatusToFrontend(data.status),
            errorMessage: data.error_message || undefined,
          });
        }
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [documents]);

  const filtered = documents.filter(doc => {
    if (typeFilter !== 'all' && doc.documentType !== typeFilter) return false;
    if (search && !doc.fileName.toLowerCase().includes(search.toLowerCase()) && !doc.aiSummary?.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).forEach(async file => {
      const tempId = `doc-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const newDoc: ManagedDocument = {
        id: tempId,
        fileName: file.name,
        fileType: file.type || 'application/octet-stream',
        fileSize: file.size,
        documentType: 'other',
        status: 'analyzing',
        uploadedAt: new Date(),
        tags: [],
      };
      addDocument(newDoc);

      try {
        const form = new FormData();
        form.append('file', file);
        form.append('document_type', 'other');
        const res = await authFetch('/api/upload-document', { method: 'POST', body: form });
        if (res.ok) {
          const { document_id } = await res.json();
          replaceDocument(tempId, { ...newDoc, id: document_id });
        } else if (res.status === 401) {
          // authFetch already triggered re-login; remove the temp card
          removeDocument(tempId);
        } else {
          const errText = await res.text().catch(() => `HTTP ${res.status}`);
          addDocument({ ...newDoc, status: 'error', errorMessage: errText });
          toast.error(`Upload failed: ${errText}`);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Upload failed';
        addDocument({ ...newDoc, status: 'error', errorMessage: msg });
        toast.error(msg);
      }
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDelete = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setDeletingId(docId);
    try {
      const res = await authFetch(`/api/document/${docId}`, { method: 'DELETE' });
      if (res.ok) {
        removeDocument(docId);
      } else if (res.status !== 401) {
        toast.error('Failed to delete document');
      }
    } catch {
      toast.error('Failed to delete document');
    } finally {
      setDeletingId(null);
    }
  };

  const readyCount = documents.filter(d => d.status === 'ready').length;
  const analyzingCount = documents.filter(d => d.status === 'analyzing').length;

  if (isLoading) return <PageLoader />;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Documents</h1>
          <p className="text-sm text-muted-foreground">
            {documents.length} documents · {analyzingCount > 0 ? `${analyzingCount} analyzing · ` : ''}{readyCount} ready
          </p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleUpload}
            accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.jpg,.jpeg,.png,.webp,.txt"
          />
          <Button onClick={() => fileInputRef.current?.click()} className="gap-2 rounded-xl">
            <Upload className="h-4 w-4" />
            Upload
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-center">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search documents…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9 rounded-xl h-9"
          />
        </div>
        <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as FilterType)}>
          <SelectTrigger className="w-[150px] rounded-xl h-9">
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            {(Object.entries(documentTypeLabels) as [DocumentType, string][]).map(([key, label]) => (
              <SelectItem key={key} value={key}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Document list */}
      <div className="space-y-2">
        {filtered.length === 0 && (
          <Card className="p-8 rounded-xl text-center">
            <FileText className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
            <p className="text-sm text-muted-foreground">No documents found. Upload a file to get started.</p>
          </Card>
        )}

        {filtered.map(doc => {
          const status = statusConfig[doc.status];
          const StatusIcon = status.icon;
          const FileIcon = fileIcon(doc.fileType);

          return (
            <Card
              key={doc.id}
              className="rounded-xl hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => navigate(`/documents/${doc.id}`)}
            >
              <div className="p-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                    <FileIcon className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <h3 className="font-semibold text-sm truncate">{doc.fileName}</h3>
                      {doc.confirmed ? (
                        <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-accent/15 text-accent">
                          <CheckCircle2 className="h-3 w-3" />
                          Confirmed
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1', status.className)}>
                          <StatusIcon className={cn('h-3 w-3', doc.status === 'analyzing' && 'animate-spin')} />
                          {status.label}
                        </Badge>
                      )}
                      <Badge variant="secondary" className="text-[10px] rounded-lg">
                        {documentTypeLabels[doc.documentType]}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                      <span>{formatFileSize(doc.fileSize)}</span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {formatDistanceToNow(new Date(doc.uploadedAt), { addSuffix: true })}
                      </span>
                      {doc.tags.length > 0 && (
                        <span className="flex items-center gap-1">
                          <Tag className="h-3 w-3" />
                          {doc.tags.length} tag{doc.tags.length !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                      disabled={deletingId === doc.id}
                      onClick={(e) => handleDelete(doc.id, e)}
                    >
                      {deletingId === doc.id
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Trash2 className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                </div>
              </div>

              {doc.status === 'error' && doc.errorMessage && (
                <div className="px-4 pb-3 flex items-start gap-2 text-destructive">
                  <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <p className="text-xs">{doc.errorMessage}</p>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
};

export default Documents;
