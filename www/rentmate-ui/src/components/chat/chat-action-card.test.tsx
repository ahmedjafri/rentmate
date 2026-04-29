import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import { ChatMessageBubble } from './ChatMessage';

const navigateSpy = vi.fn();
const sendMessageMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

vi.mock('@/graphql/client', () => ({
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
}));

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
}));

function renderBubble(
  message: Parameters<typeof ChatMessageBubble>[0]['message'],
  extraProps: Partial<Parameters<typeof ChatMessageBubble>[0]> = {},
) {
  return render(
    <MemoryRouter
      initialEntries={['/']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/" element={<ChatMessageBubble message={message} {...extraProps} />} />
      </Routes>
    </MemoryRouter>,
  );
}

test('renders property action cards with nested unit links', async () => {
  renderBubble({
    id: 'msg-1',
    role: 'assistant',
    content: 'Created property',
    timestamp: new Date('2026-04-11T12:00:00Z'),
    senderName: 'RentMate',
    senderType: 'ai',
    messageType: 'action',
    actionCard: {
      kind: 'property',
      title: '123 Main St',
      summary: 'Created property at 123 Main St.',
      fields: [{ label: 'Type', value: 'Multi-family' }],
      links: [{ label: 'Open property', entityType: 'property', entityId: 'prop-1' }],
      units: [{ uid: 'unit-1', label: '1A', propertyId: 'prop-1' }],
    },
  });

  expect(screen.getByText('Property created')).toBeInTheDocument();
  expect(screen.getByText('123 Main St')).toBeInTheDocument();
  expect(screen.getByText('Type:')).toBeInTheDocument();
  expect(screen.getByText('Multi-family')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /open property/i }));
  expect(navigateSpy).toHaveBeenCalledWith('/properties/prop-1');

  fireEvent.click(screen.getByRole('button', { name: /1A/i }));
  expect(navigateSpy).toHaveBeenCalledWith('/properties/prop-1?unit=unit-1#unit-unit-1');
});

test('renders document action cards with open action', async () => {
  renderBubble({
    id: 'msg-doc-1',
    role: 'assistant',
    content: 'Created document',
    timestamp: new Date('2026-04-11T12:00:00Z'),
    senderName: 'RentMate',
    senderType: 'ai',
    messageType: 'action',
    actionCard: {
      kind: 'document',
      title: 'notice.pdf',
      summary: 'Generated notice PDF.',
      fields: [{ label: 'Type', value: 'notice' }],
      links: [{ label: 'Open document', entityType: 'document', entityId: 'doc-1' }],
    },
  });

  expect(screen.getByText('Document created')).toBeInTheDocument();
  expect(screen.getByText('notice.pdf')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /open document/i }));
  expect(navigateSpy).toHaveBeenCalledWith('/documents/doc-1');
});

function questionMessage(): Parameters<typeof ChatMessageBubble>[0]['message'] {
  return {
    id: 'msg-q-1',
    role: 'assistant',
    content: 'Should I approve the $450 plumber quote?',
    timestamp: new Date('2026-04-25T12:00:00Z'),
    senderName: 'RentMate',
    senderType: 'ai',
    messageType: 'action',
    actionCard: {
      kind: 'question',
      title: 'Should I approve the $450 plumber quote?',
    },
  };
}

test('renders question action cards with an inline reply form', async () => {
  sendMessageMock.mockResolvedValueOnce(undefined);
  renderBubble(questionMessage(), { conversationId: 'conv-42' });

  expect(screen.getByText('Agent needs your input')).toBeInTheDocument();
  // The card title shows the question.
  expect(screen.getAllByText(/Should I approve/).length).toBeGreaterThan(0);
  // Inline reply form is mounted.
  const textarea = screen.getByPlaceholderText('Type your answer…') as HTMLTextAreaElement;
  expect(textarea).toBeInTheDocument();

  fireEvent.change(textarea, { target: { value: 'Yes go ahead' } });
  fireEvent.click(screen.getByRole('button', { name: /send/i }));

  await waitFor(() => {
    expect(sendMessageMock).toHaveBeenCalledWith({
      conversationId: 'conv-42',
      body: 'Yes go ahead',
    });
  });
});

test('question card renders the Answered state when an answer body is provided', () => {
  renderBubble(questionMessage(), {
    conversationId: 'conv-42',
    questionAnsweredByContent: 'Approved.',
  });

  expect(screen.getByText('Answered')).toBeInTheDocument();
  expect(screen.getByText('Approved.')).toBeInTheDocument();
  // Reply textarea is hidden once answered.
  expect(screen.queryByPlaceholderText('Type your answer…')).not.toBeInTheDocument();
});

test('renders agent-review messages as a status card, not a chat reply', () => {
  renderBubble({
    id: 'msg-review-1',
    role: 'assistant',
    content: 'Sent confirmation request to Marcus.',
    timestamp: new Date('2026-04-25T12:00:00Z'),
    senderName: 'RentMate',
    senderType: 'ai',
    messageType: 'action',
    reviewCard: {
      status: 'waiting',
      summary: 'Sent confirmation request to Marcus.',
      nextStep: 'Await tenant access window.',
    },
  });

  // Distinct status card surface — no "🤖 Agent review" preamble, no
  // "Summary" label, no agent sender row.
  expect(screen.queryByText(/🤖 Agent review/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/^Summary$/)).not.toBeInTheDocument();
  expect(screen.getByText('Waiting')).toBeInTheDocument();
  expect(screen.getByText('Agent update')).toBeInTheDocument();
  expect(screen.getByText('Sent confirmation request to Marcus.')).toBeInTheDocument();
  expect(screen.getByText(/Await tenant access window\./)).toBeInTheDocument();
});
