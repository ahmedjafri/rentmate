import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ChatInput, PendingAttachment } from './ChatInput';
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';

// Wrapper that manages attachments state like ChatPanel does
function TestWrapper({ uploadFile }: { uploadFile: (file: File) => Promise<{ id: string; filename: string } | null> }) {
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  return (
    <ChatInput
      onSend={vi.fn()}
      uploadFile={uploadFile}
      attachments={attachments}
      setAttachments={setAttachments}
    />
  );
}

describe('ChatInput file attachments', () => {
  it('shows an attachment chip after file selection', async () => {
    let resolveUpload: (val: { id: string; filename: string }) => void;
    const uploadFile = vi.fn(() => new Promise<{ id: string; filename: string } | null>((resolve) => {
      resolveUpload = resolve;
    }));

    render(<TestWrapper uploadFile={uploadFile} />);

    // Find the hidden file input
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeTruthy();

    // Simulate file selection
    const file = new File(['pdf-content'], 'test-lease.pdf', { type: 'application/pdf' });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });

    // uploadFile should have been called
    expect(uploadFile).toHaveBeenCalledWith(file);

    // Chip should appear with "uploading…"
    await waitFor(() => {
      expect(screen.getByText('test-lease.pdf')).toBeInTheDocument();
    });
    expect(screen.getByText('uploading…')).toBeInTheDocument();

    // Resolve the upload
    await act(async () => {
      resolveUpload!({ id: 'doc-123', filename: 'test-lease.pdf' });
    });

    // "uploading…" should disappear, chip remains
    await waitFor(() => {
      expect(screen.queryByText('uploading…')).not.toBeInTheDocument();
    });
    expect(screen.getByText('test-lease.pdf')).toBeInTheDocument();
  });
});
