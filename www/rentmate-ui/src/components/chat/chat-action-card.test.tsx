import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import { ChatMessageBubble } from './ChatMessage';

const navigateSpy = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

function renderBubble(message: Parameters<typeof ChatMessageBubble>[0]['message']) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<ChatMessageBubble message={message} />} />
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
  expect(screen.getByText('Created units')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /open property/i }));
  expect(navigateSpy).toHaveBeenCalledWith('/properties/prop-1');

  fireEvent.click(screen.getByRole('button', { name: /1A/i }));
  expect(navigateSpy).toHaveBeenCalledWith('/properties/prop-1?unit=unit-1#unit-unit-1');
});
